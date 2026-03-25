# Walaa Connector (Odoo 18)

This module connects Odoo Sales and Products with your Walaa app.

## What it does

- Stores Walaa configuration per Odoo company.
- Receives product sync triggers from Walaa (`POST /walaa/sync/products`).
- Sends confirmed sales orders to Walaa immediately.
- Sends product sync requests to Walaa immediately when trigger is received.
- Provides integration logs and manual resend actions.

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

### How to copy `walaa_connector` into addons path

1. Find your Odoo `addons_path`:

```bash
grep -E '^addons_path' /etc/odoo/odoo.conf
```

2. Copy module folder to one of the listed addons directories (example target: `/opt/odoo/custom/addons`):

```bash
cp -R /Users/mr.dhawi/Desktop/owalaa/walaa_connector /opt/odoo/custom/addons/
```

3. If Odoo runs as user `odoo`, fix ownership:

```bash
sudo chown -R odoo:odoo /opt/odoo/custom/addons/walaa_connector
```

4. Restart Odoo:

```bash
sudo systemctl restart odoo
```

### Optional CLI install

```bash
odoo-bin -d <database_name> -i walaa_connector --addons-path=<your_addons_paths>
```

### If upgrading from an older version

1. Upgrade module:

```bash
odoo-bin -d <database_name> -u walaa_connector --addons-path=<your_addons_paths>
```

2. In Odoo, go to `Settings` -> `Technical` -> `Scheduled Actions`.
3. Search for `Walaa Connector: Process Queue` and disable it if it still exists.

## Configuration

1. Go to `Settings` -> `General Settings`.
2. Find section `Walaa Connector`.
3. Select the correct company (top-right company switcher).
4. Fill fields:
   - `Enable Walaa Connector`: enable integration for this company.
   - `Walaa Brand Token`: unique brand token for this company.
   - `Walaa Inbound API Key`: static key used by Walaa when calling Odoo trigger endpoint.
   - `Walaa Base URL`: base URL of Walaa API, example `https://api.walaa.example`.
   - `Walaa Product Sync Path`: product endpoint path, example `/api/odoo/products/sync`.
   - `Walaa Order Path`: order endpoint path, example `/api/odoo/orders`.
5. Click `Save`.
6. Click `Test Walaa Connection`.

## Usage

### 1) Product sync (Walaa -> Odoo trigger -> Walaa)

Walaa calls Odoo:

- Method: `POST`
- URL: `https://<your-odoo-domain>/walaa/sync/products`
- Header: `X-Walaa-API-Key: <company_inbound_api_key>`
- Body:

```json
{
  "brand_token": "your_brand_token"
}
```

Success response:

```json
{
  "status": "sent",
  "job_id": 123
}
```

HTTP status on success: `200`.

What happens after request:

- Odoo creates an integration log job.
- Odoo processes it immediately in the same request.
- Odoo exports all active saleable products for that company in batches of 200.
- Odoo sends product batches to Walaa product endpoint.

### 2) Order sync (Odoo -> Walaa)

When a Sales Order is confirmed:

- Odoo creates an `order_push` integration job record.
- Job is processed immediately and sends order payload to Walaa order endpoint.
- Headers include:
  - `Content-Type: application/json`
  - `X-Brand-Token: <company_brand_token>`
  - `Idempotency-Key: <generated_key>`

If brand token is missing:

- Order confirmation is not blocked.
- Job is stored as `failed` with error message.

## Delivery Behavior

- No cron is used for sending requests.
- Product and order sends are synchronous (direct).
- On failure, job state is `failed`.
- Use `Resend` to retry manually.

## Monitoring and Operations

Go to:

- `Sales` -> `Configuration` -> `Walaa Connector` -> `Integration Logs`

You can:

- Filter by `Queued`, `Processing`, `Sent`, `Failed`.
- Open each log to inspect payload, response, and error.
- Click `Resend` on failed jobs.
- Use list action `Resend to Walaa` for selected failed rows.

## API Error Reference (Product Trigger)

Possible responses from `POST /walaa/sync/products`:

- `400` invalid JSON body.
- `400` missing `brand_token`.
- `404` unknown `brand_token` (no matching company).
- `401` invalid or missing `X-Walaa-API-Key`.
- `403` connector disabled for company.
- `200` sent successfully.
- `502` send failed (check integration log for details).

## Multi-company Notes

- Configuration is per company.
- `Walaa Brand Token` must be unique across companies.
- Trigger request `brand_token` decides which company configuration is used.

## Troubleshooting

1. **No jobs created on order confirmation**
   - Ensure company has Walaa connector enabled.
   - Ensure order actually reached `sale` or `done` state.

2. **Jobs stuck in queued**
   - This should not happen in direct-send mode.
   - Open the job and click `Resend`.

3. **Jobs failing with HTTP errors**
   - Verify `Walaa Base URL` and endpoint paths.
   - Verify Walaa server can accept Odoo IP / traffic.
   - Inspect response body in Integration Log.

4. **Product trigger returns 401**
   - Verify header `X-Walaa-API-Key` exactly matches company setting.

5. **Product trigger returns 404 unknown token**
   - Verify `brand_token` exists in company `Walaa Brand Token`.

## Security Recommendations

- Use HTTPS for both Odoo and Walaa endpoints.
- Rotate `Walaa Inbound API Key` periodically.
- Restrict public endpoint access at reverse proxy/WAF level where possible.
- Keep Integration Logs access limited to trusted users.

## Uninstall

1. Go to `Apps`.
2. Find `Walaa Connector`.
3. Click `Uninstall`.
4. Restart Odoo service.
5. Remove module folder from addons path if no longer needed.
