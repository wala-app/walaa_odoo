from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import SavepointCase


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class TestWalaaConnector(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Walaa Customer",
                "email": "customer@example.com",
                "phone": "+96890000000",
                "company_id": False,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Walaa Test Product",
                "default_code": "WALAA-SKU-1",
                "list_price": 120.0,
                "sale_ok": True,
                "type": "consu",
            }
        )

    def setUp(self):
        super().setUp()
        self.company.write(
            {
                "walaa_enabled": True,
                "walaa_brand_token": "brand-main",
                "walaa_base_url": "https://walaa.example",
                "walaa_order_path": "/api/orders",
            }
        )

    def _create_sale_order(self):
        return self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "company_id": self.company.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "name": self.product.name,
                            "product_id": self.product.id,
                            "product_uom_qty": 2,
                            "product_uom": self.product.uom_id.id,
                            "price_unit": 100.0,
                        },
                    )
                ],
            }
        )

    def test_order_confirm_sends_order_immediately(self):
        order = self._create_sale_order()

        with patch(
            "odoo.addons.walaa_connector.models.sale_order.requests.post",
            return_value=FakeResponse(200, "ok"),
        ) as post_mock:
            order.action_confirm()
        self.assertEqual(post_mock.call_count, 1)
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["order"]["customer"]["email"], "customer@example.com")
        self.assertEqual(payload["order"]["customer"]["phone"], "+96890000000")

    def test_settings_related_fields_write_company_values(self):
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.company.id,
                "walaa_enabled": True,
                "walaa_brand_token": "brand-updated-from-settings",
                "walaa_base_url": "https://new-walaa.example",
                "walaa_order_path": "/orders/v2",
            }
        )
        settings.write({"walaa_enabled": True})

        self.company.invalidate_cache()
        self.assertEqual(self.company.walaa_brand_token, "brand-updated-from-settings")
        self.assertEqual(self.company.walaa_base_url, "https://new-walaa.example")
        self.assertEqual(self.company.walaa_order_path, "/orders/v2")

    def test_missing_brand_token_skips_order_send(self):
        self.company.walaa_brand_token = False
        order = self._create_sale_order()

        with patch(
            "odoo.addons.walaa_connector.models.sale_order.requests.post",
            return_value=FakeResponse(200, "ok"),
        ) as post_mock:
            order.action_confirm()
        self.assertEqual(post_mock.call_count, 0)

    def test_product_sync_response_builder(self):
        response_payload = self.company._walaa_build_product_sync_response(
            trigger_payload={"brand_token": "brand-main"}, limit=50, offset=0
        )
        self.assertIn("products", response_payload)
        self.assertIn("pagination", response_payload)
        self.assertLessEqual(response_payload["pagination"]["count"], 50)
        self.assertEqual(response_payload["sync_mode"], "pull")

    def test_product_sync_response_builder_rejects_invalid_limit(self):
        with self.assertRaises(ValidationError):
            self.company._walaa_build_product_sync_response(
                trigger_payload={"brand_token": "brand-main"},
                limit=0,
                offset=0,
            )
