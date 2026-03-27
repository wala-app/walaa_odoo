import logging

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    walaa_sent = fields.Boolean(string="Sent To Walaa", default=False, copy=False)
    walaa_last_error = fields.Text(string="Walaa Last Error", copy=False)

    def write(self, vals):
        result = super().write(vals)
        if "state" in vals:
            self._walaa_try_send_ready_orders()
        return result

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._walaa_try_send_ready_orders()
        return orders

    def _walaa_try_send_ready_orders(self):
        ready_states = {"paid", "done", "invoiced"}
        for order in self:
            if order.walaa_sent or order.state not in ready_states:
                continue
            success, error_message = order._walaa_send_pos_payload_direct()
            if success:
                order.sudo().write({"walaa_sent": True, "walaa_last_error": False})
            elif error_message:
                order.sudo().write({"walaa_last_error": error_message})

    def _walaa_send_pos_payload_direct(self):
        self.ensure_one()
        company = self.company_id
        if not company.walaa_enabled:
            return False, "Connector disabled."
        if not company.walaa_brand_token:
            _logger.warning(
                "Skipping Walaa POS order push for %s because brand token is missing.",
                self.name,
            )
            return False, "Brand token is missing."

        try:
            company._walaa_validate_outbound_config(require_brand_token=True)
            endpoint = company._walaa_order_url()
            headers = company._walaa_outbound_headers(
                idempotency_key=f"pos-{self.id}-state-{self.state}"
            )
            response = requests.post(
                endpoint,
                json=self._walaa_build_pos_payload(),
                headers=headers,
                timeout=15,
            )
            if not (200 <= response.status_code < 300):
                msg = "HTTP %s: %s" % (response.status_code, (response.text or "")[:1000])
                _logger.warning(
                    "Walaa POS order push failed for %s with %s", self.name, msg
                )
                return False, msg
            return True, False
        except Exception as exc:  # pragma: no cover - defensive logging
            _logger.exception("Walaa POS order push failed for %s", self.name)
            return False, str(exc)

    def _walaa_build_pos_payload(self):
        self.ensure_one()
        lines = []
        for line in self.lines:
            lines.append(
                {
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "product_name": line.full_product_name or line.product_id.display_name,
                    "sku": line.product_id.default_code,
                    "qty": line.qty,
                    "unit_price": line.price_unit,
                    "discount": line.discount,
                    "subtotal": line.price_subtotal,
                    "total": line.price_subtotal_incl,
                }
            )

        return {
            "event": "pos_order_paid",
            "order": {
                "id": self.id,
                "name": self.name,
                "pos_reference": self.pos_reference,
                "state": self.state,
                "order_datetime": fields.Datetime.to_string(self.date_order),
                "company": {
                    "id": self.company_id.id,
                    "name": self.company_id.name,
                },
                "customer": {
                    "id": self.partner_id.id if self.partner_id else False,
                    "name": self.partner_id.name if self.partner_id else False,
                    "email": self.partner_id.email if self.partner_id else False,
                    "phone": self.partner_id.phone if self.partner_id else False,
                },
                "currency": self.currency_id.name,
                "amount_tax": self.amount_tax,
                "amount_total": self.amount_total,
                "amount_paid": self.amount_paid,
                "amount_return": self.amount_return,
                "lines": lines,
            },
        }
