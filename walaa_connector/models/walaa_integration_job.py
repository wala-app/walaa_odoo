import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class OutboundRequestError(Exception):
    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class WalaaIntegrationJob(models.Model):
    _name = "walaa.integration.job"
    _description = "Walaa Integration Job"
    _order = "create_date desc, id desc"

    _REQUEST_TIMEOUT_SECONDS = 15
    _PRODUCT_BATCH_SIZE = 200

    job_type = fields.Selection(
        [("product_sync", "Product Sync"), ("order_push", "Order Push")],
        required=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    sale_order_id = fields.Many2one("sale.order", index=True)
    payload_json = fields.Text(required=True)
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("sent", "Sent"),
            ("failed", "Failed"),
        ],
        required=True,
        default="queued",
        index=True,
    )
    attempt_count = fields.Integer(default=0)
    next_retry_at = fields.Datetime(default=fields.Datetime.now, index=True)
    idempotency_key = fields.Char(index=True)
    last_error = fields.Text()
    response_status = fields.Integer()
    response_body = fields.Text()

    @api.model
    def enqueue_order_push(self, order):
        company = order.company_id
        values = {
            "job_type": "order_push",
            "company_id": company.id,
            "sale_order_id": order.id,
            "payload_json": json.dumps(order._walaa_build_order_payload()),
            "idempotency_key": order._walaa_order_event_idempotency_key(),
            "state": "queued",
            "next_retry_at": fields.Datetime.now(),
        }

        if not company.walaa_enabled:
            values.update(
                {
                    "state": "failed",
                    "last_error": _(
                        "Order was not sent because Walaa connector is disabled on the company."
                    ),
                    "next_retry_at": False,
                }
            )
        elif not company.walaa_brand_token:
            values.update(
                {
                    "state": "failed",
                    "last_error": _(
                        "Order was not sent because Walaa Brand Token is missing on the company."
                    ),
                    "next_retry_at": False,
                }
            )

        return self.sudo().create(values)

    @api.model
    def enqueue_product_sync(self, company, trigger_payload=None):
        payload = trigger_payload or {}
        values = {
            "job_type": "product_sync",
            "company_id": company.id,
            "payload_json": json.dumps(payload),
            "state": "queued",
            "next_retry_at": fields.Datetime.now(),
        }
        return self.sudo().create(values)

    @api.model
    def create_and_send_order_push(self, order):
        job = self.enqueue_order_push(order)
        if job.state == "queued":
            job._process_job()
        return job

    @api.model
    def create_and_send_product_sync(self, company, trigger_payload=None):
        job = self.enqueue_product_sync(company, trigger_payload=trigger_payload)
        if job.state == "queued":
            job._process_job()
        return job

    @api.model
    def cron_process_queue(self, limit=100):
        """
        Backward compatibility for databases that still have the old cron record.
        In direct-send mode this only processes any leftover queued jobs.
        """
        jobs = self.sudo().search([("state", "=", "queued")], limit=limit, order="id asc")
        for job in jobs:
            with self.env.cr.savepoint():
                job._process_job()
        return True

    def action_resend(self):
        failed_jobs = self.filtered(lambda j: j.state == "failed")
        if not failed_jobs:
            raise UserError(_("Only failed jobs can be resent."))
        failed_jobs.write(
            {
                "state": "queued",
                "attempt_count": 0,
                "next_retry_at": fields.Datetime.now(),
                "last_error": False,
                "response_status": False,
                "response_body": False,
            }
        )
        for job in failed_jobs:
            job._process_job()
        return True

    def _process_job(self):
        self.ensure_one()
        if self.state != "queued":
            return

        self.write(
            {
                "state": "processing",
                "attempt_count": self.attempt_count + 1,
            }
        )

        try:
            if self.job_type == "order_push":
                status_code, response_body = self._send_order_payload()
            else:
                status_code, response_body = self._send_product_payload()

            self.write(
                {
                    "state": "sent",
                    "last_error": False,
                    "response_status": status_code,
                    "response_body": self._truncate(response_body),
                    "next_retry_at": False,
                }
            )
        except (OutboundRequestError, ValidationError) as exc:
            status_code = getattr(exc, "status_code", False)
            response_body = getattr(exc, "response_body", False)
            self._queue_or_fail(
                str(exc),
                response_status=status_code,
                response_body=response_body,
            )
        except Exception as exc:  # pragma: no cover - defensive catch-all
            _logger.exception("Unexpected error processing Walaa job %s", self.id)
            self._queue_or_fail(_("Unexpected error: %s") % str(exc))

    def _queue_or_fail(self, error_message, response_status=False, response_body=False):
        self.ensure_one()
        self.write(
            {
                "state": "failed",
                "last_error": error_message,
                "response_status": response_status,
                "response_body": self._truncate(response_body),
                "next_retry_at": False,
            }
        )

    def _send_order_payload(self):
        self.ensure_one()
        company = self.company_id
        company._walaa_validate_outbound_config(require_brand_token=True)

        payload = json.loads(self.payload_json or "{}")
        endpoint = company._walaa_compose_url(company.walaa_order_path)
        headers = company._walaa_outbound_headers(idempotency_key=self.idempotency_key)
        return self._post_json(endpoint, payload, headers)

    def _send_product_payload(self):
        self.ensure_one()
        company = self.company_id
        company._walaa_validate_outbound_config(require_brand_token=True)

        endpoint = company._walaa_compose_url(company.walaa_product_sync_path)
        headers = company._walaa_outbound_headers()
        trigger_payload = json.loads(self.payload_json or "{}")

        products = self.env["product.product"].sudo().search(
            [
                ("active", "=", True),
                ("sale_ok", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company.id),
            ],
            order="id asc",
        )

        total = len(products)
        batch_count = max(1, (total + self._PRODUCT_BATCH_SIZE - 1) // self._PRODUCT_BATCH_SIZE)
        status_code = 200
        response_body = ""

        for batch_index in range(batch_count):
            start = batch_index * self._PRODUCT_BATCH_SIZE
            end = start + self._PRODUCT_BATCH_SIZE
            batch_products = products[start:end]
            payload = {
                "event": "product_sync",
                "sync_mode": "full",
                "job_id": self.id,
                "batch_index": batch_index + 1,
                "batch_count": batch_count,
                "company": {
                    "id": company.id,
                    "name": company.name,
                },
                "trigger": trigger_payload,
                "products": [
                    self._serialize_product(product, company.currency_id.name)
                    for product in batch_products
                ],
            }
            status_code, response_body = self._post_json(endpoint, payload, headers)

        return status_code, response_body

    def _serialize_product(self, product, currency_code):
        variant_attributes = []
        for value in product.product_template_attribute_value_ids:
            variant_attributes.append(
                {
                    "attribute": value.attribute_id.name,
                    "value": value.name,
                }
            )

        return {
            "id": product.id,
            "template_id": product.product_tmpl_id.id,
            "sku": product.default_code,
            "name": product.display_name,
            "price": product.lst_price,
            "currency": currency_code,
            "barcode": product.barcode,
            "active": product.active,
            "category": product.categ_id.name,
            "variant_attributes": variant_attributes,
        }

    def _post_json(self, url, payload, headers):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise OutboundRequestError(str(exc)) from exc

        response_body = response.text or ""
        if not (200 <= response.status_code < 300):
            raise OutboundRequestError(
                _("Walaa API returned HTTP %s") % response.status_code,
                status_code=response.status_code,
                response_body=response_body,
            )
        return response.status_code, response_body

    @staticmethod
    def _truncate(value, max_length=5000):
        if not value:
            return False
        if len(value) <= max_length:
            return value
        return value[:max_length]
