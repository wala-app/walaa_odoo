import json
from unittest.mock import patch

import requests

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
        self.job_model = self.env["walaa.integration.job"].sudo()
        self.company.write(
            {
                "walaa_enabled": True,
                "walaa_brand_token": "brand-main",
                "walaa_base_url": "https://walaa.example",
                "walaa_product_sync_path": "/api/products/sync",
                "walaa_order_path": "/api/orders",
                "walaa_inbound_api_key": "inbound-secret",
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
            "odoo.addons.walaa_connector.models.walaa_integration_job.requests.post",
            return_value=FakeResponse(200, "ok"),
        ):
            order.action_confirm()

        job = self.job_model.search(
            [
                ("sale_order_id", "=", order.id),
                ("job_type", "=", "order_push"),
            ],
            limit=1,
        )
        self.assertTrue(job)
        self.assertEqual(job.state, "sent")
        self.assertTrue(job.idempotency_key)

        payload = json.loads(job.payload_json)
        self.assertEqual(payload["order"]["customer"]["email"], "customer@example.com")
        self.assertEqual(payload["order"]["customer"]["phone"], "+96890000000")

    def test_settings_related_fields_write_company_values(self):
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.company.id,
                "walaa_enabled": True,
                "walaa_brand_token": "brand-updated-from-settings",
                "walaa_base_url": "https://new-walaa.example",
                "walaa_product_sync_path": "/products/v2/sync",
                "walaa_order_path": "/orders/v2",
                "walaa_inbound_api_key": "new-key",
            }
        )
        settings.write({"walaa_enabled": True})

        self.company.invalidate_cache()
        self.assertEqual(self.company.walaa_brand_token, "brand-updated-from-settings")
        self.assertEqual(self.company.walaa_base_url, "https://new-walaa.example")
        self.assertEqual(self.company.walaa_product_sync_path, "/products/v2/sync")
        self.assertEqual(self.company.walaa_order_path, "/orders/v2")
        self.assertEqual(self.company.walaa_inbound_api_key, "new-key")

    def test_missing_brand_token_creates_failed_job(self):
        self.company.walaa_brand_token = False
        order = self._create_sale_order()

        order.action_confirm()

        job = self.job_model.search(
            [
                ("sale_order_id", "=", order.id),
                ("job_type", "=", "order_push"),
            ],
            limit=1,
        )
        self.assertTrue(job)
        self.assertEqual(job.state, "failed")
        self.assertIn("Brand Token", job.last_error)

    def test_failed_send_marks_job_failed_directly(self):
        job = self.job_model.create(
            {
                "job_type": "order_push",
                "company_id": self.company.id,
                "payload_json": json.dumps({"event": "order_confirmed", "order": {"id": 10}}),
                "idempotency_key": "order-10-confirm-test",
                "state": "queued",
            }
        )

        with patch(
            "odoo.addons.walaa_connector.models.walaa_integration_job.requests.post",
            side_effect=requests.RequestException("network error"),
        ):
            job._process_job()
            job.invalidate_cache()
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(job.state, "failed")
            self.assertFalse(job.next_retry_at)

    def test_product_sync_sends_immediately(self):
        with patch(
            "odoo.addons.walaa_connector.models.walaa_integration_job.requests.post",
            return_value=FakeResponse(200, "ok"),
        ):
            job = self.job_model.create_and_send_product_sync(
                self.company,
                trigger_payload={"brand_token": "brand-main"},
            )
        self.assertEqual(job.job_type, "product_sync")
        self.assertEqual(job.state, "sent")
        self.assertEqual(job.company_id.id, self.company.id)

    def test_resend_sends_failed_job_immediately(self):
        job = self.job_model.create(
            {
                "job_type": "order_push",
                "company_id": self.company.id,
                "payload_json": json.dumps({"event": "order_confirmed", "order": {"id": 11}}),
                "idempotency_key": "order-11-confirm-test",
                "state": "failed",
                "attempt_count": 5,
                "last_error": "boom",
            }
        )

        with patch(
            "odoo.addons.walaa_connector.models.walaa_integration_job.requests.post",
            return_value=FakeResponse(200, "ok"),
        ):
            job.action_resend()
        job.invalidate_cache()

        self.assertEqual(job.state, "sent")
        self.assertEqual(job.attempt_count, 1)
        self.assertFalse(job.last_error)
