from unittest.mock import patch

from odoo.tests.common import SavepointCase


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class TestWalaa(SavepointCase):
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
                "standard_price": 75.0,
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
            "odoo.addons.walaa.models.sale_order.requests.post",
            return_value=FakeResponse(200, "ok"),
        ) as post_mock:
            order.action_confirm()
        self.assertEqual(post_mock.call_count, 1)
        endpoint = post_mock.call_args.args[0]
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(endpoint, "https://walaa.example/api/odoo/orders")
        self.assertEqual(payload["order"]["customer"]["email"], "customer@example.com")
        self.assertEqual(payload["order"]["customer"]["phone"], "+96890000000")

    def test_settings_related_fields_write_company_values(self):
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.company.id,
                "walaa_enabled": True,
                "walaa_brand_token": "brand-updated-from-settings",
                "walaa_base_url": "https://new-walaa.example",
            }
        )
        settings.write({"walaa_enabled": True})

        self.company.invalidate_cache()
        self.assertEqual(self.company.walaa_brand_token, "brand-updated-from-settings")
        self.assertEqual(self.company.walaa_base_url, "https://new-walaa.example")

    def test_missing_brand_token_skips_order_send(self):
        self.company.walaa_brand_token = False
        order = self._create_sale_order()

        with patch(
            "odoo.addons.walaa.models.sale_order.requests.post",
            return_value=FakeResponse(200, "ok"),
        ) as post_mock:
            order.action_confirm()
        self.assertEqual(post_mock.call_count, 0)

    def test_product_sync_response_builder(self):
        response_payload = self.company._walaa_build_product_sync_response(
            trigger_payload={"brand_token": "brand-main"}
        )
        self.assertIn("products", response_payload)
        self.assertEqual(response_payload["sync_mode"], "pull")
        self.assertEqual(response_payload["total_products"], len(response_payload["products"]))
        product = next(
            (p for p in response_payload["products"] if p.get("sku") == "WALAA-SKU-1"),
            None,
        )
        self.assertTrue(product)
        self.assertIn("cost", product)
        self.assertIn("image_base64", product)
        self.assertEqual(product["cost"], 75.0)

    def test_manual_sync_all_products_now(self):
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.company.id,
                "walaa_enabled": True,
                "walaa_brand_token": "brand-main",
                "walaa_base_url": "https://walaa.example",
            }
        )

        with patch(
            "odoo.addons.walaa.models.res_config_settings.requests.post",
            return_value=FakeResponse(200, "ok"),
        ) as post_mock:
            action = settings.action_sync_all_products_now()

        self.assertEqual(post_mock.call_count, 1)
        endpoint = post_mock.call_args.args[0]
        kwargs = post_mock.call_args.kwargs
        self.assertEqual(endpoint, "https://walaa.example/api/odoo/products/sync")
        self.assertEqual(kwargs["headers"]["X-Brand-Token"], "brand-main")
        self.assertEqual(kwargs["json"]["sync_mode"], "full_push")
        self.assertEqual(kwargs["json"]["total_products"], len(kwargs["json"]["products"]))
        product = next(
            (p for p in kwargs["json"]["products"] if p.get("sku") == "WALAA-SKU-1"),
            None,
        )
        self.assertTrue(product)
        self.assertIn("cost", product)
        self.assertIn("image_base64", product)
        self.assertEqual(product["cost"], 75.0)
        self.assertEqual(action["type"], "ir.actions.client")

    def test_pos_payload_includes_gift_when_set(self):
        """Verify that _walaa_build_pos_payload includes gift data."""
        pos_config = self.env["pos.config"].create({"name": "Walaa Test POS"})
        session = self.env["pos.session"].create(
            {"config_id": pos_config.id, "user_id": self.env.uid}
        )
        order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "partner_id": self.partner.id,
                "company_id": self.company.id,
                "lines": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "full_product_name": self.product.name,
                            "qty": 1,
                            "price_unit": 100.0,
                            "price_subtotal": 100.0,
                            "price_subtotal_incl": 100.0,
                        },
                    )
                ],
                "amount_total": 100.0,
                "amount_tax": 0.0,
                "amount_paid": 100.0,
                "amount_return": 0.0,
            }
        )

        # Without gift
        payload = order._walaa_build_pos_payload()
        self.assertIsNone(payload["order"]["gift"])

        # With gift
        order.write({"walaa_gift_id": 42, "walaa_gift_reward_id": 7})
        payload = order._walaa_build_pos_payload()
        self.assertEqual(payload["order"]["gift"]["id"], 42)
        self.assertEqual(payload["order"]["gift"]["reward_id"], 7)

    def test_gifts_controller_returns_empty_when_disabled(self):
        """Controller returns empty gifts list when walaa is disabled."""
        from odoo.addons.walaa.controllers.main import WalaaConnectorController

        self.company.walaa_enabled = False
        controller = WalaaConnectorController()
        # Simulate the method call (no HTTP context needed)
        result = controller.get_customer_gifts(customer_phone="+96890000000")
        self.assertEqual(result["gifts"], [])
        self.assertIn("disabled", result.get("error", ""))
