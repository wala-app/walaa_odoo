from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    WALAA_BASE_URL = "https://api.walaa.app"
    WALAA_ORDER_PATH = "/api/odoo/orders"
    WALAA_PRODUCT_SYNC_PATH = "/api/odoo/products/sync"
    # "Sync All Products Now" sends the catalog in pages of this many products.
    WALAA_PRODUCT_SYNC_BATCH_SIZE = 50

    walaa_enabled = fields.Boolean(string="Enable Walaa", default=False)
    walaa_brand_token = fields.Char(string="Walaa Brand Token", copy=False)

    # Odoo 19 ignores ``_sql_constraints``; constraints are declared with models.Constraint.
    _walaa_brand_token_unique = models.Constraint(
        "unique(walaa_brand_token)",
        "Walaa brand token must be unique.",
    )

    def _walaa_compose_url(self, path):
        self.ensure_one()
        base_url = self.WALAA_BASE_URL
        route_path = (path or "").strip()
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
        if require_brand_token and not self.walaa_brand_token:
            raise ValidationError(_("Missing Walaa configuration values: Walaa Brand Token"))

    def _walaa_order_url(self):
        self.ensure_one()
        return self._walaa_compose_url(self.WALAA_ORDER_PATH)

    def _walaa_product_sync_url(self):
        self.ensure_one()
        return self._walaa_compose_url(self.WALAA_PRODUCT_SYNC_PATH)

    def _walaa_syncable_products(self):
        """Active saleable products of this company (or shared), oldest first."""
        self.ensure_one()
        domain = [
            ("active", "=", True),
            ("sale_ok", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.id),
        ]
        return self.env["product.product"].sudo().search(domain, order="id asc")

    def _walaa_build_product_sync_batches(self, trigger_payload=None, batch_size=None):
        """Yield the full-push payload in pages of ``batch_size`` products.

        Walaa upserts every product it receives, so the catalog can be sent in several
        requests. Images are serialized one page at a time to keep requests small.
        An empty catalog still yields one (empty) page.
        """
        self.ensure_one()
        trigger_payload = trigger_payload or {}
        batch_size = batch_size or self.WALAA_PRODUCT_SYNC_BATCH_SIZE
        products = self._walaa_syncable_products()
        total = len(products)
        total_pages = max(1, -(-total // batch_size))
        for page in range(1, total_pages + 1):
            chunk = products[(page - 1) * batch_size : page * batch_size]
            yield {
                "event": "product_sync",
                "sync_mode": "full_push",
                "company": {
                    "id": self.id,
                    "name": self.name,
                },
                "trigger": trigger_payload,
                "total_products": total,
                "page": page,
                "total_pages": total_pages,
                "page_size": batch_size,
                "products": [
                    self._walaa_serialize_product(product, self.currency_id.name) for product in chunk
                ],
            }

    def _walaa_build_product_sync_response(self, trigger_payload=None):
        self.ensure_one()
        trigger_payload = trigger_payload or {}
        products = self._walaa_syncable_products()

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
                self._walaa_serialize_product(product, self.currency_id.name) for product in products
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

        image_value = product.image_1920 or product.product_tmpl_id.image_1920
        if isinstance(image_value, bytes):
            image_value = image_value.decode("utf-8")

        return {
            "id": product.id,
            "template_id": product.product_tmpl_id.id,
            "sku": product.default_code,
            "name": product.display_name,
            "price": product.lst_price,
            "cost": product.standard_price,
            "currency": currency_code,
            "barcode": product.barcode,
            "active": product.active,
            "category": product.categ_id.name,
            "image_base64": image_value,
            "variant_attributes": variant_attributes,
        }
