# Walaa Connector (Odoo 18)

This module connects Odoo Sales and Products with your Walaa app.

## What it does

- Stores Walaa configuration per Odoo company.
- Receives product sync triggers from Walaa (`POST /walaa/sync/products`).
- Returns products directly in the Odoo response (pull mode).
- Sends confirmed sales orders to Walaa immediately.

## Requirements

- Odoo 18 (self-hosted).
- Installed Odoo modules: `sale`, `product`, `base`.
- Walaa API endpoints reachable from the Odoo server.
- Python `requests` package available in Odoo environment.

## Installation

1. Copy module folder `walaa_connector` into your Odoo addons path.
2. Restart Odoo service.
3. In Odoo UI:
   - Go to `Apps`.
   - Click `Update Apps List`.
   - Search for `Walaa Connector`.
   - Click `Install`.

### Optional CLI install

```bash
odoo-bin -d <database_name> -i walaa_connector --addons-path=<your_addons_paths>
```

### If upgrading from an older version

1. Upgrade module:

```bash
odoo-bin -d <database_name> -u walaa_connector --addons-path=<your_addons_paths>
```

## Configuration

1. Go to `Settings` -> `General Settings`.
2. Find section `Walaa Connector`.
3. Select the correct company (top-right company switcher).
4. Fill fields:
   - `Enable Walaa Connector`: enable integration for this company.
   - `Walaa Brand Token`: unique brand token for this company.
   - `Walaa Base URL`: base URL of Walaa API, example `https://api.walaa.example`.
   - `Walaa Order Path`: order endpoint path, example `/api/odoo/orders`.
5. Click `Save`.
6. Click `Test Walaa Connection`.

## Usage

### 1) Product sync (Walaa -> Odoo response)

Walaa calls Odoo:

- Method: `POST`
- URL: `https://<your-odoo-domain>/walaa/sync/products`
- Authentication: token-only (`brand_token` in JSON body)
- Body:

```json
{
  "brand_token": "your_brand_token",
  "limit": 200,
  "offset": 0
}
```

Success response:

```json
{
  "event": "product_sync",
  "sync_mode": "pull",
  "status": "sent",
  "pagination": {
    "limit": 200,
    "offset": 0,
    "count": 200,
    "total": 540,
    "has_more": true,
    "next_offset": 200
  },
  "products": [
    {
      "id": 10,
      "sku": "SKU-10",
      "name": "Example Product"
    }
  ]
}
```

HTTP status on success: `200`.

What happens after request:

- Odoo processes it immediately in the same request.
- Odoo returns active saleable products for that company.
- Use `limit`/`offset` to page through the full catalog.

### 2) Order sync (Odoo -> Walaa)

When a Sales Order is confirmed:

- Odoo immediately sends order payload to Walaa order endpoint.
- Headers include:
  - `Content-Type: application/json`
  - `X-Brand-Token: <company_brand_token>`
  - `Idempotency-Key: <generated_key>`

If brand token is missing:

- Order confirmation is not blocked.
- Order push is skipped.

## Delivery Behavior

- No cron is used for sending requests.
- Product sync is pull-response from Odoo.
- Order push is synchronous (direct) from Odoo to Walaa.
- On failure, Odoo writes warning/error logs in server logs (does not block order confirmation).

## API Error Reference (Product Trigger)

Possible responses from `POST /walaa/sync/products`:

- `400` invalid JSON body.
- `400` missing `brand_token`.
- `400` invalid pagination (`limit`/`offset`).
- `404` unknown `brand_token` (no matching company).
- `403` connector disabled for company.
- `200` sent successfully.
- `500` internal error.

## Multi-company Notes

- Configuration is per company.
- `Walaa Brand Token` must be unique across companies.
- Trigger request `brand_token` decides which company configuration is used.

## Troubleshooting

1. **Order not sent on confirmation**
   - Ensure company has Walaa connector enabled.
   - Ensure order actually reached `sale` or `done` state.

2. **Order push failing with HTTP errors**
   - Verify `Walaa Base URL` and endpoint paths.
   - Verify Walaa server can accept Odoo IP / traffic.
   - Inspect Odoo server logs.

3. **Product trigger returns 404 unknown token**
   - Verify `brand_token` exists in company `Walaa Brand Token`.

4. **Install/upgrade still fails after code fix**
   - Restart Odoo service.
   - Update Apps List from Odoo Apps menu.
   - Upgrade module again: `odoo-bin -d <database_name> -u walaa_connector --addons-path=<your_addons_paths>`.

## Security Recommendations

- Use HTTPS for both Odoo and Walaa endpoints.
- Rotate `Walaa Brand Token` periodically.
- Restrict public endpoint access at reverse proxy/WAF level where possible.

## Uninstall

1. Go to `Apps`.
2. Find `Walaa Connector`.
3. Click `Uninstall`.
4. Restart Odoo service.
5. Remove module folder from addons path if no longer needed.
