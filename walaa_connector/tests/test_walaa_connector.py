import json
from unittest.mock import patch

import requests

from odoo.tests.common import SavepointCase


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

    def test_order_confirm_enqueues_order_job(self):
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
        self.assertEqual(job.state, "queued")
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

    def test_retry_backoff_reaches_failed_state_after_five_attempts(self):
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
            for attempt in range(1, 6):
                job._process_job()
                job.invalidate_cache()
                self.assertEqual(job.attempt_count, attempt)
                if attempt < 5:
                    self.assertEqual(job.state, "queued")
                    self.assertTrue(job.next_retry_at)
                else:
                    self.assertEqual(job.state, "failed")
                    self.assertFalse(job.next_retry_at)

    def test_enqueue_product_sync_job(self):
        job = self.job_model.enqueue_product_sync(
            self.company,
            trigger_payload={"brand_token": "brand-main"},
        )
        self.assertEqual(job.job_type, "product_sync")
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.company_id.id, self.company.id)

    def test_resend_resets_failed_job(self):
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

        job.action_resend()
        job.invalidate_cache()

        self.assertEqual(job.state, "queued")
        self.assertEqual(job.attempt_count, 0)
        self.assertFalse(job.last_error)
