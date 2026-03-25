from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    walaa_enabled = fields.Boolean(string="Enable Walaa Connector", default=False)
    walaa_brand_token = fields.Char(string="Walaa Brand Token", copy=False)
    walaa_base_url = fields.Char(string="Walaa Base URL")
    walaa_product_sync_path = fields.Char(
        string="Walaa Product Sync Path", default="/api/odoo/products/sync"
    )
    walaa_order_path = fields.Char(string="Walaa Order Path", default="/api/odoo/orders")
    walaa_inbound_api_key = fields.Char(string="Walaa Inbound API Key", copy=False)

    _sql_constraints = [
        (
            "walaa_brand_token_unique",
            "unique(walaa_brand_token)",
            "Walaa brand token must be unique.",
        ),
    ]

    def _walaa_compose_url(self, path):
        self.ensure_one()
        base_url = (self.walaa_base_url or "").strip().rstrip("/")
        route_path = (path or "").strip()
        if not base_url:
            raise ValidationError(_("Walaa Base URL is required."))
        if not route_path:
            raise ValidationError(_("Walaa endpoint path is required."))
        if not route_path.startswith("/"):
            route_path = f"/{route_path}"
        return f"{base_url}{route_path}"

    def _walaa_outbound_headers(self, idempotency_key=None):
        self.ensure_one()
        headers = {"Content-Type": "application/json"}
        if self.walaa_brand_token:
            headers["X-Brand-Token"] = self.walaa_brand_token
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _walaa_validate_outbound_config(self, require_brand_token=False):
        self.ensure_one()
        if not self.walaa_enabled:
            raise ValidationError(_("Walaa connector is disabled for this company."))
        missing = []
        if not self.walaa_base_url:
            missing.append(_("Walaa Base URL"))
        if not self.walaa_product_sync_path:
            missing.append(_("Walaa Product Sync Path"))
        if not self.walaa_order_path:
            missing.append(_("Walaa Order Path"))
        if require_brand_token and not self.walaa_brand_token:
            missing.append(_("Walaa Brand Token"))
        if missing:
            raise ValidationError(
                _("Missing Walaa configuration values: %s") % ", ".join(missing)
            )
