# Walaa (Odoo 18)

This module connects Odoo Sales and Products with your Walaa app.

## What it does

- Stores Walaa configuration per Odoo company.
- Receives product sync triggers from Walaa (`POST /walaa/sync/products`).
- Returns full products directly in the Odoo response (pull mode, no paging).
- Adds a manual button in Odoo settings to push all products to Walaa in one request.
- Sends confirmed Sales Orders and paid PoS Orders to Walaa immediately.
- Uses fixed outbound paths (not editable): `/api/odoo/orders` and `/api/odoo/products/sync`.

## Requirements

- Odoo 18 (self-hosted).
- Installed Odoo modules: `sale`, `product`, `base`, `point_of_sale`.
- Walaa API endpoints reachable from the Odoo server.
- Python `requests` package available in Odoo environment.

## Installation

1. Copy module folder `walaa` into your Odoo addons path.
2. Restart Odoo service.
3. In Odoo UI:
   - Go to `Apps`.
   - Click `Update Apps List`.
   - Search for `Walaa`.
   - Click `Install`.

### Optional CLI install

```bash
odoo-bin -d <database_name> -i walaa --addons-path=<your_addons_paths>
```

### If upgrading from an older version

1. Upgrade module:

```bash
odoo-bin -d <database_name> -u walaa --addons-path=<your_addons_paths>
```

## Configuration

1. Go to `Settings` -> `General Settings`.
2. Find section `Walaa`.
3. Select the correct company (top-right company switcher).
4. Fill fields:
   - `Enable Walaa`: enable integration for this company.
   - `Walaa Brand Token`: unique brand token for this company.
   - `Walaa Base URL`: base URL of Walaa API, example `https://api.walaa.example`.
5. Click `Save`.
6. Click `Test Walaa Connection`.
7. For immediate product push, click `Sync All Products Now`.

## Usage

### 1) Product sync (Walaa -> Odoo response)

Walaa calls Odoo:

- Method: `POST`
- URL: `https://<your-odoo-domain>/walaa/sync/products`
- Authentication: token-only (`brand_token` in JSON body)
- Body:

```json
{
  "brand_token": "your_brand_token"
}
```

Success response:

```json
{
  "event": "product_sync",
  "sync_mode": "pull",
  "status": "sent",
  "total_products": 540,
  "products": [
    {
      "id": 10,
      "sku": "SKU-10",
      "name": "Example Product",
      "price": 10.0,
      "cost": 7.5,
      "image_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
    }
  ]
}
```

HTTP status on success: `200`.

What happens after request:

- Odoo processes it immediately in the same request.
- Odoo returns all active saleable products for that company (no paging).
- Product item includes sale price, cost, and image (`image_base64`).

### 1.1) Product sync (Odoo manual button -> Walaa)

From Odoo:

1. Go to `Settings` -> `General Settings`.
2. Open `Walaa` section.
3. Click `Sync All Products Now`.

What happens:

- Odoo sends one full payload (all active + saleable products) to `Walaa Base URL + /api/odoo/products/sync`.
- Headers include `Content-Type`, `X-Brand-Token`, and `Idempotency-Key`.

### Product Sync test in VS Code

1. Install VS Code extension `REST Client` (by Huachao Mao).
2. Open file `vscode_product_sync_test.http` in this module.
3. Edit variables at top of file:
   - `@baseUrl` (your Odoo URL)
   - `@brandToken` (a valid brand token from Odoo settings)
4. Click `Send Request` above each request.

Test file includes:
- Success full product pull
- Missing token (`400`)
- Unknown token (`404`)

### 2) Order sync (Odoo Sales + PoS -> Walaa)

When a Sales Order is confirmed or a PoS Order reaches paid/done/invoiced:

- Odoo immediately sends order payload to `Walaa Base URL + /api/odoo/orders`.
- Headers include:
  - `Content-Type: application/json`
  - `X-Brand-Token: <company_brand_token>`
  - `Idempotency-Key: <generated_key>`

If brand token is missing:

- Order processing is not blocked.
- Order push is skipped.

## Delivery Behavior

- No cron is used for sending requests.
- Product sync is pull-response from Odoo.
- Manual product push sends all products in one request (no paging).
- Sales + PoS order push is synchronous (direct) from Odoo to Walaa.
- On failure, Odoo writes warning/error logs in server logs (does not block Sales/PoS flows).

## API Error Reference (Product Trigger)

Possible responses from `POST /walaa/sync/products`:

- `400` invalid JSON body.
- `400` missing `brand_token`.
- `404` unknown `brand_token` (no matching company).
- `403` connector disabled for company.
- `200` sent successfully.
- `500` internal error.

## Multi-company Notes

- Configuration is per company.
- `Walaa Brand Token` must be unique across companies.
- Trigger request `brand_token` decides which company configuration is used.

## Troubleshooting

1. **Order not sent**
   - Ensure company has Walaa connector enabled.
   - Ensure Sales Order reached `sale`/`done` or PoS Order reached `paid`/`done`/`invoiced`.

2. **Order push failing with HTTP errors**
   - Verify `Walaa Base URL` is correct.
   - Verify Walaa server can accept Odoo IP / traffic.
   - Inspect Odoo server logs.

3. **Product trigger returns 404 unknown token**
   - Verify `brand_token` exists in company `Walaa Brand Token`.

4. **Install/upgrade still fails after code fix**
   - Restart Odoo service.
   - Update Apps List from Odoo Apps menu.
   - Upgrade module again: `odoo-bin -d <database_name> -u walaa --addons-path=<your_addons_paths>`.

## Security Recommendations

- Use HTTPS for both Odoo and Walaa endpoints.
- Rotate `Walaa Brand Token` periodically.
- Restrict public endpoint access at reverse proxy/WAF level where possible.

## Uninstall

1. Go to `Apps`.
2. Find `Walaa`.
3. Click `Uninstall`.
4. Restart Odoo service.
5. Remove module folder from addons path if no longer needed.
