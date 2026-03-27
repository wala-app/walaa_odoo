import logging
import uuid

import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        result = super().action_confirm()
        for order in self:
            if order.state not in ("sale", "done"):
                continue
            order._walaa_send_order_payload_direct()

        return result

    def _walaa_send_order_payload_direct(self):
        self.ensure_one()
        company = self.company_id
        if not company.walaa_enabled:
            return False
        if not company.walaa_brand_token:
            _logger.warning(
                "Skipping Walaa order push for order %s because brand token is missing.",
                self.name,
            )
            return False

        try:
            company._walaa_validate_outbound_config(require_brand_token=True)
            endpoint = company._walaa_order_url()
            headers = company._walaa_outbound_headers(
                idempotency_key=self._walaa_order_event_idempotency_key()
            )
            response = requests.post(
                endpoint,
                json=self._walaa_build_order_payload(),
                headers=headers,
                timeout=15,
            )
            if not (200 <= response.status_code < 300):
                _logger.warning(
                    "Walaa order push failed for order %s with HTTP %s: %s",
                    self.name,
                    response.status_code,
                    (response.text or "")[:1000],
                )
                return False
            return True
        except Exception:
            _logger.exception("Walaa order push failed for order %s", self.name)
            return False

    def _walaa_build_order_payload(self):
        self.ensure_one()
        line_payload = []
        for line in self.order_line.filtered(lambda l: not l.display_type):
            line_payload.append(
                {
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "product_name": line.name,
                    "sku": line.product_id.default_code,
                    "qty": line.product_uom_qty,
                    "unit_price": line.price_unit,
                    "discount": line.discount,
                    "subtotal": line.price_subtotal,
                    "total": line.price_total,
                    "tax_percent": sum(line.tax_id.mapped("amount")),
                }
            )

        return {
            "event": "order_confirmed",
            "order": {
                "id": self.id,
                "name": self.name,
                "client_reference": self.client_order_ref,
                "state": self.state,
                "confirmation_timestamp": fields.Datetime.to_string(self.date_order),
                "company": {
                    "id": self.company_id.id,
                    "name": self.company_id.name,
                },
                "customer": {
                    "id": self.partner_id.id,
                    "name": self.partner_id.name,
                    "email": self.partner_id.email,
                    "phone": self.partner_id.phone,
                },
                "currency": self.currency_id.name,
                "amount_untaxed": self.amount_untaxed,
                "amount_tax": self.amount_tax,
                "amount_total": self.amount_total,
                "lines": line_payload,
            },
        }

    def _walaa_order_event_idempotency_key(self):
        self.ensure_one()
        return f"order-{self.id}-confirm-{uuid.uuid4().hex}"
