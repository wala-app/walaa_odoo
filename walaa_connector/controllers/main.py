import json

from odoo import http
from odoo.http import request


class WalaaConnectorController(http.Controller):
    @http.route(
        "/walaa/sync/products",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def walaa_sync_products(self, **kwargs):
        del kwargs

        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or "{}")
        except ValueError:
            return request.make_json_response(
                {"error": "invalid_json", "message": "Request body must be valid JSON."},
                status=400,
            )

        if not isinstance(payload, dict):
            return request.make_json_response(
                {"error": "invalid_payload", "message": "JSON body must be an object."},
                status=400,
            )

        brand_token = payload.get("brand_token")
        if not brand_token:
            return request.make_json_response(
                {
                    "error": "missing_brand_token",
                    "message": "brand_token is required.",
                },
                status=400,
            )

        company = (
            request.env["res.company"]
            .sudo()
            .search([("walaa_brand_token", "=", brand_token)], limit=1)
        )
        if not company:
            return request.make_json_response(
                {
                    "error": "unknown_brand_token",
                    "message": "No company found for provided brand_token.",
                },
                status=404,
            )

        provided_key = request.httprequest.headers.get("X-Walaa-API-Key")
        if not provided_key or provided_key != (company.walaa_inbound_api_key or ""):
            return request.make_json_response(
                {
                    "error": "unauthorized",
                    "message": "Invalid X-Walaa-API-Key.",
                },
                status=401,
            )

        if not company.walaa_enabled:
            return request.make_json_response(
                {
                    "error": "connector_disabled",
                    "message": "Walaa connector is disabled for this company.",
                },
                status=403,
            )

        job = (
            request.env["walaa.integration.job"]
            .sudo()
            .create_and_send_product_sync(company, trigger_payload=payload)
        )
        if job.state == "sent":
            return request.make_json_response(
                {"status": "sent", "job_id": job.id},
                status=200,
            )

        return request.make_json_response(
            {
                "status": "failed",
                "job_id": job.id,
                "error": job.last_error,
            },
            status=502,
        )
