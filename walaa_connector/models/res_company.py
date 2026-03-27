from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    WALAA_ORDER_PATH = "/api/odoo/orders"
    WALAA_PRODUCT_SYNC_PATH = "/api/odoo/products/sync"

    walaa_enabled = fields.Boolean(string="Enable Walaa Connector", default=False)
    walaa_brand_token = fields.Char(string="Walaa Brand Token", copy=False)
    walaa_base_url = fields.Char(string="Walaa Base URL")

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
        if require_brand_token and not self.walaa_brand_token:
            missing.append(_("Walaa Brand Token"))
        if missing:
            raise ValidationError(
                _("Missing Walaa configuration values: %s") % ", ".join(missing)
            )

    def _walaa_order_url(self):
        self.ensure_one()
        return self._walaa_compose_url(self.WALAA_ORDER_PATH)

    def _walaa_product_sync_url(self):
        self.ensure_one()
        return self._walaa_compose_url(self.WALAA_PRODUCT_SYNC_PATH)

    def _walaa_build_full_product_sync_payload(self, trigger_payload=None):
        self.ensure_one()
        trigger_payload = trigger_payload or {}
        product_model = self.env["product.product"].sudo()
        domain = [
            ("active", "=", True),
            ("sale_ok", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.id),
        ]
        products = product_model.search(domain, order="id asc")
        return {
            "event": "product_sync",
            "sync_mode": "full_push",
            "company": {
                "id": self.id,
                "name": self.name,
            },
            "trigger": trigger_payload,
            "total_products": len(products),
            "products": [
                self._walaa_serialize_product(product, self.currency_id.name)
                for product in products
            ],
        }

    def _walaa_build_product_sync_response(self, trigger_payload=None):
        self.ensure_one()
        trigger_payload = trigger_payload or {}
        product_model = self.env["product.product"].sudo()
        domain = [
            ("active", "=", True),
            ("sale_ok", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.id),
        ]
        products = product_model.search(domain, order="id asc")

        return {
            "event": "product_sync",
            "sync_mode": "pull",
            "company": {
                "id": self.id,
                "name": self.name,
            },
            "trigger": trigger_payload,
            "total_products": len(products),
            "products": [
                self._walaa_serialize_product(product, self.currency_id.name)
                for product in products
            ],
        }

    def _walaa_serialize_product(self, product, currency_code):
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
