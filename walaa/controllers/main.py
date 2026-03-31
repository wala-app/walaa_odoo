import json
import logging
import re

import requests

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)


class WalaaConnectorController(http.Controller):
    @staticmethod
    def _normalize_phone(value):
        return re.sub(r"[\s\-\(\)]", "", value or "")

    def _find_partner_by_phone(self, raw_phone):
        partner_model = request.env["res.partner"].sudo()
        clean_phone = self._normalize_phone(raw_phone)
        if not clean_phone:
            return partner_model.browse()

        candidates = [clean_phone, raw_phone]
        candidates = [value for value in candidates if value]
        # Exact match on phone/mobile first (fast path)
        partner = partner_model.search(
            [
                "|",
                ("phone", "in", candidates),
                ("mobile", "in", candidates),
            ],
            limit=1,
            order="id asc",
        )
        if partner:
            return partner

        # Fallback: normalized compare in Python when existing values contain spaces/symbols.
        sample = partner_model.search(
            [
                "|",
                ("phone", "!=", False),
                ("mobile", "!=", False),
            ],
            limit=1000,
            order="id desc",
        )
        for rec in sample:
            if self._normalize_phone(rec.phone) == clean_phone:
                return rec
            if self._normalize_phone(rec.mobile) == clean_phone:
                return rec
        return partner_model.browse()

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

        if not company.walaa_enabled:
            return request.make_json_response(
                {
                    "error": "connector_disabled",
                    "message": "Walaa connector is disabled for this company.",
                },
                status=403,
            )

        try:
            response_payload = company._walaa_build_product_sync_response(
                trigger_payload=payload
            )
        except ValidationError as exc:
            return request.make_json_response(
                {
                    "status": "failed",
                    "error": str(exc),
                },
                status=400,
            )
        except Exception as exc:
            return request.make_json_response(
                {
                    "status": "failed",
                    "error": str(exc),
                },
                status=500,
            )

        response_payload.update({"status": "sent"})
        return request.make_json_response(response_payload, status=200)

    @http.route(
        "/walaa/pos/customer_gifts",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def get_customer_gifts(self, customer_phone, **kwargs):
        """Fetch Walaa rewards/gifts for a customer by phone number."""
        company = request.env.company
        if not company.walaa_enabled:
            return {"gifts": [], "count": 0, "error": "Walaa connector is disabled."}
        if not company.walaa_brand_token:
            return {"gifts": [], "count": 0, "error": "Walaa is not fully configured."}

        customer_uid = re.sub(r"[\s\-\(\)]", "", customer_phone or "")
        if not customer_uid:
            return {"gifts": [], "count": 0, "error": "No phone number provided."}

        base_url = company.WALAA_BASE_URL
        url = f"{base_url}/api/odoo/customers/{customer_uid}/gifts"
        headers = {"X-Brand-Token": company.walaa_brand_token}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    "gifts": data.get("userGifts", []),
                    "count": data.get("count", 0),
                }
            _logger.warning(
                "Walaa gifts API returned %s for phone %s", response.status_code, customer_uid
            )
            return {"gifts": [], "count": 0, "error": f"API error {response.status_code}"}
        except Exception as exc:
            _logger.exception("Walaa gifts API call failed for phone %s", customer_uid)
            return {"gifts": [], "count": 0, "error": str(exc)}

    @http.route(
        "/walaa/pos/order_requests_today",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def get_order_requests_today(self, **kwargs):
        del kwargs
        company = request.env.company
        if not company.walaa_enabled:
            return {
                "orderRequests": [],
                "count": 0,
                "error": "Walaa connector is disabled.",
            }
        if not company.walaa_brand_token:
            return {
                "orderRequests": [],
                "count": 0,
                "error": "Walaa is not fully configured.",
            }

        base_url = company.WALAA_BASE_URL
        url = f"{base_url}/api/odoo/order-requests/today"
        headers = {"X-Brand-Token": company.walaa_brand_token}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                requests_list = data.get("orderRequests", [])
                return {
                    "orderRequests": requests_list if isinstance(requests_list, list) else [],
                    "count": data.get("count", 0),
                }
            return {
                "orderRequests": [],
                "count": 0,
                "error": f"API error {response.status_code}",
            }
        except Exception as exc:
            _logger.exception("Walaa order requests API call failed")
            return {"orderRequests": [], "count": 0, "error": str(exc)}

    @http.route(
        "/walaa/pos/order_request_select",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def select_order_request(self, order_request, **kwargs):
        del kwargs
        if not isinstance(order_request, dict):
            return {"error": "Invalid order request payload."}

        phone = self._normalize_phone(order_request.get("phoneNumber"))
        name = (order_request.get("customerName") or "").strip() or "Walaa Customer"

        if not phone:
            return {"error": "Selected request has no phone number."}

        partner = self._find_partner_by_phone(phone)
        created = False
        if not partner:
            partner = request.env["res.partner"].sudo().create(
                {
                    "name": name,
                    "phone": phone,
                }
            )
            created = True
        else:
            write_vals = {}
            if not partner.name and name:
                write_vals["name"] = name
            if not partner.phone and phone:
                write_vals["phone"] = phone
            if write_vals:
                partner.sudo().write(write_vals)

        # Fire-and-forget: notify Walaa that this order request was selected.
        document_id = order_request.get("documentId") or order_request.get("id")
        company = request.env.company
        if document_id and company.walaa_enabled and company.walaa_brand_token:
            base_url = company.WALAA_BASE_URL
            notify_url = f"{base_url}/api/order/requests/{document_id}"
            try:
                requests.post(
                    notify_url,
                    headers={"X-Brand-Token": company.walaa_brand_token},
                    timeout=(5, 0.001),
                )
            except requests.exceptions.ReadTimeout:
                pass  # expected – we don't need the response
            except Exception:
                _logger.warning(
                    "Walaa order request notify failed for documentId %s", document_id
                )

        partner_payload = {
            "id": partner.id,
            "name": partner.name,
            "display_name": partner.display_name or partner.name,
            "phone": partner.phone,
            "mobile": partner.mobile,
        }
        return {
            "partner": partner_payload,
            "created": created,
            "orderRequest": order_request,
        }
