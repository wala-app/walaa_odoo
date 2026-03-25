import uuid

from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        result = super().action_confirm()
        job_model = self.env["walaa.integration.job"].sudo()

        for order in self:
            if order.state not in ("sale", "done"):
                continue
            existing_job = job_model.search(
                [
                    ("sale_order_id", "=", order.id),
                    ("job_type", "=", "order_push"),
                    ("state", "in", ("queued", "processing", "sent")),
                ],
                limit=1,
            )
            if existing_job:
                continue
            job_model.enqueue_order_push(order)

        return result

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
