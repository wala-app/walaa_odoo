import json

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    WALAA_BASE_URL = "https://api.walaa.app"
    WALAA_ORDER_PATH = "/api/odoo/orders"
    WALAA_PRODUCT_SYNC_PATH = "/api/odoo/products/sync"
    # "Sync All Products Now" sends the catalog in pages of at most this many products...
    WALAA_PRODUCT_SYNC_BATCH_SIZE = 10
    # ...and at most this many bytes of JSON per request. The Walaa API rejects bigger
    # bodies (Django's default limit is 2.5 MB, and Cloudflare in front answers HTTP 413).
    WALAA_PRODUCT_SYNC_MAX_BYTES = 2_000_000
    # Room kept for the rest of the request (company, page numbers, trigger...).
    WALAA_PRODUCT_SYNC_ENVELOPE_BYTES = 16_384
    # Image sizes tried, largest first, when one product alone would not fit in a request.
    WALAA_PRODUCT_IMAGE_FIELDS = ("image_1920", "image_1024", "image_512", "image_256", "image_128")

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

    def _walaa_fit_product(self, product, currency_code, budget):
        """Return ``(image_field, size)``: the largest image size that keeps this product's
        JSON within ``budget`` bytes, and the resulting size. ``image_field`` is False when
        even the smallest image does not fit (the product is then sent without image)."""
        for image_field in self.WALAA_PRODUCT_IMAGE_FIELDS:
            entry = self._walaa_serialize_product(product, currency_code, image_field)
            size = len(json.dumps(entry))
            if size <= budget:
                return image_field, size
        entry = self._walaa_serialize_product(product, currency_code, False)
        return False, len(json.dumps(entry))

    def _walaa_plan_product_sync_pages(self, batch_size, max_bytes):
        """Group the syncable products into pages of at most ``batch_size`` products and at
        most ``max_bytes`` of JSON. Returns a list of pages of ``(product, image_field)``."""
        currency_code = self.currency_id.name
        budget = max_bytes - self.WALAA_PRODUCT_SYNC_ENVELOPE_BYTES
        pages, page, page_bytes = [], [], 0
        for product in self._walaa_syncable_products():
            image_field, size = self._walaa_fit_product(product, currency_code, budget)
            size += 2  # ", " between products in the list
            if page and (len(page) >= batch_size or page_bytes + size > budget):
                pages.append(page)
                page, page_bytes = [], 0
            page.append((product, image_field))
            page_bytes += size
        if page or not pages:
            pages.append(page)
        return pages

    def _walaa_build_product_sync_batches(self, trigger_payload=None, batch_size=None, max_bytes=None):
        """Yield the full-push payload in pages small enough for the Walaa API.

        Walaa upserts every product it receives, so the catalog can be sent in several
        requests. A page holds at most ``batch_size`` products and ``max_bytes`` of JSON;
        a product whose full-size image alone is too big is sent with a smaller image.
        Images are serialized one page at a time. An empty catalog still yields one page.
        """
        self.ensure_one()
        trigger_payload = trigger_payload or {}
        batch_size = batch_size or self.WALAA_PRODUCT_SYNC_BATCH_SIZE
        max_bytes = max_bytes or self.WALAA_PRODUCT_SYNC_MAX_BYTES
        plan = self._walaa_plan_product_sync_pages(batch_size, max_bytes)
        total = sum(len(page) for page in plan)
        total_pages = len(plan)
        for page, chunk in enumerate(plan, 1):
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
                    self._walaa_serialize_product(product, self.currency_id.name, image_field)
                    for product, image_field in chunk
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

    def _walaa_serialize_product(self, product, currency_code, image_field="image_1920"):
        variant_attributes = []
        for value in product.product_template_attribute_value_ids:
            variant_attributes.append(
                {
                    "attribute": value.attribute_id.name,
                    "value": value.name,
                }
            )

        # Odoo 20: binary fields return a BinaryValue holding the raw content (no longer
        # base64 bytes), so encode it explicitly. An empty image is falsy.
        image = image_field and (product[image_field] or product.product_tmpl_id[image_field])
        image_value = image.to_base64() if image else False

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
