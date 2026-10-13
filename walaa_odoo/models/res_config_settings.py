import requests

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    walaa_enabled = fields.Boolean(related="company_id.walaa_enabled", readonly=False)
    walaa_brand_token = fields.Char(related="company_id.walaa_brand_token", readonly=False)

    def action_test_walaa_connection(self):
        self.ensure_one()
        company = self.company_id
        company._walaa_validate_outbound_config(require_brand_token=True)

        payload = {
            "event": "odoo_test",
            "company_id": company.id,
            "timestamp": fields.Datetime.now().isoformat(),
        }
        url = company._walaa_order_url()
        headers = company._walaa_outbound_headers(
            idempotency_key=f"test-company-{company.id}"
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise UserError(_("Walaa connection test failed: %s") % str(exc)) from exc

        if 200 <= response.status_code < 300:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Walaa"),
                    "message": _("Connection test succeeded."),
                    "type": "success",
                    "sticky": False,
                },
            }

        body = (response.text or "")[:1000]
        raise UserError(
            _("Walaa connection test failed with HTTP %s: %s")
            % (response.status_code, body)
        )

    def action_sync_all_products_now(self):
        self.ensure_one()
        company = self.company_id
        company._walaa_validate_outbound_config(require_brand_token=True)

        url = company._walaa_product_sync_url()
        sync_id = f"manual-product-sync-{company.id}-{fields.Datetime.now().timestamp()}"
        sent = 0
        total = 0
        pages = 0

        for payload in company._walaa_build_product_sync_batches(
            trigger_payload={"source": "odoo_manual_button"}
        ):
            total, pages = payload["total_products"], payload["total_pages"]
            page = payload["page"]
            headers = company._walaa_outbound_headers(idempotency_key=f"{sync_id}-page-{page}")
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=60)
            except requests.RequestException as exc:
                raise UserError(
                    _("Product sync failed on batch %(page)s/%(pages)s (%(sent)s of %(total)s products already sent): %(error)s",
                      page=page, pages=pages, sent=sent, total=total, error=str(exc))
                ) from exc

            if not (200 <= response.status_code < 300):
                raise UserError(
                    _("Product sync failed on batch %(page)s/%(pages)s with HTTP %(status)s (%(sent)s of %(total)s products already sent): %(body)s",
                      page=page, pages=pages, status=response.status_code, sent=sent, total=total,
                      body=(response.text or "")[:1000])
                )
            sent += len(payload["products"])

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Walaa"),
                "message": _(
                    "Product sync sent successfully (%(total)s products in %(pages)s batches).",
                    total=total, pages=pages,
                ),
                "type": "success",
                "sticky": False,
            },
        }
