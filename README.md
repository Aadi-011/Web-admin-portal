# YatraFlow Site Admin Dashboard

Responsive site-operations dashboard, Python HTTP API, persistent SQLite development store, and optional Microsoft SQL Server schema. The existing workspace did not contain YatraFlow's mobile app, production backend/API, production schema, identity provider, GPS feed, emergency-service integration or push provider. This project therefore runs as a standalone dashboard and **does not claim end-to-end mobile/production integration**.

## Run locally (development sample)

Requires Python 3.10+; the default SQLite mode uses only the Python standard library.

```bash
python3 server.py
```

Open `http://localhost:5173` and sign in with the **local sample-only** account:

- Email: `admin@yatraflow.test`
- Password: `YatraDemo!2026`

This account is created only for the local SQLite development database. Do not use it in a deployed environment. On first start, the server creates `data/yatraflow.sqlite3` and adds clearly labelled illustrative rows for Amber Fort, City Palace and Hawa Mahal. The dashboard displays a development/sample banner. Sample occupancy, visitor records, alerts, broadcasts and historical footfall are not connected to the YatraFlow mobile app, GPS or live sensors. No ML forecast is seeded or shown as real; forecast values remain unavailable unless a genuine model record is written to `crowd_forecasts`.

The development server binds to `0.0.0.0`. Use HTTPS and a hardened production web server/reverse proxy for deployment.

## Authentication and roles

The API requires a database-backed administrator account for dashboard routes. Passwords use PBKDF2-HMAC-SHA256 with per-account random salts; session tokens are random, stored as hashes, held in an HttpOnly/SameSite cookie, and expire after eight hours. Login attempts are rate-limited. Administrator access changes, login/logout, incident lifecycle changes, assignments, notes, notifications, broadcasts, visitor checkout and mobile-ingestion events are recorded in `audit_log`. Deactivated accounts have their sessions revoked.

- **Super Admin** — site selection across configured sites; administrator provisioning and access changes; operational views and audit log.
- **Site Admin** — site-scoped operational access, site-scoped audit log, and creation of Emergency Operator / Analytics Viewer accounts.
- **Emergency Operator** — site-scoped incident and visitor operations; no administrator management or audit-log access.
- **Analytics Viewer** — read-only analytics access; no incident, visitor-PII, broadcast or administrator-management routes.

Role checks and site scoping are enforced in the API, not just the interface. The initial administrator account for SQL Server is bootstrapped through environment settings; the demo seed script alone intentionally does not create a usable password.

## Microsoft SQL Server

No SQL Server host, credentials or production schema were supplied, and no SQL Server connection has been verified in this workspace. The SQL Server schema below is an integration starting point, not a substitute for review against YatraFlow's organization-owned schema.

1. Install Microsoft ODBC Driver 18 for SQL Server on the server host.
2. Install the Python ODBC bridge:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

3. Create a database and least-privilege application login. Run `sqlserver/schema.sql` (idempotent create/migrate script) in that database.
4. Optionally run `sqlserver/seed-demo.sql` **only in a development database**. It sets `app_metadata.data_mode=demo`; the dashboard then labels those records as illustrative sample data. The seed inserts historical illustrative footfall only and no forecast model result.
5. Copy `.env.example` to `.env`. Set `YATRAFLOW_DB_ENGINE=sqlserver`, SQL Server connection values, and strong administrator bootstrap credentials:

   ```dotenv
   YATRAFLOW_BOOTSTRAP_ADMIN_EMAIL=admin@example.org
   YATRAFLOW_BOOTSTRAP_ADMIN_PASSWORD=<a secret of at least 12 characters>
   YATRAFLOW_BOOTSTRAP_ADMIN_NAME=Site Administrator
   YATRAFLOW_BOOTSTRAP_ADMIN_ROLE=super_admin
   YATRAFLOW_BOOTSTRAP_SITE_ID=1
   ```

   Supply actual values through the host's secret manager/environment. Do not paste credentials into chat, commit `.env`, or use the sample credentials above in production. The server creates the bootstrap account only if that email does not already exist; changing an existing account's password must be done through an approved, separate credential-rotation procedure.
6. Restart the service and verify `GET /api/health` reports a connected SQL Server and ready schema. SQL Server mode will not silently fall back to SQLite sample data.

Do not set `YATRAFLOW_DATA_MODE=configured` or `integrated` to relabel data. SQLite is always marked `demo`; its mode cannot be overridden to hide the sample banner. SQL Server reads `app_metadata.data_mode` and normalizes every non-demo value to `configured` (not “integrated”). The demo seed marks the database `demo`. Only after removing/replacing sample rows and verifying the source with the YatraFlow data owner should an authorized database administrator update that metadata. `YATRAFLOW_DATA_MODE=demo` is supported as a conservative override.

## Data sources and honest status

- Occupancy is derived from database visitor sessions or timestamped crowd observations; a configured site aggregate is exposed as a database snapshot. Capacity comes from the site's configured database record.
- Zone density is shown from timestamped `crowd_observations`; if no observations exist in configured mode, the map shows **no observation** rather than reusing seeded density as “live.” The map artwork is an illustrative site-zone layout, not geospatial/GPS mapping.
- Historical charts query `footfall`. The local and optional demo seed series are synthetic illustrative examples and are explicitly labelled as development sample data.
- Forecasts are read only from `crowd_forecasts`, which includes a forecast date, model name, generation time, and source. No model service or sample prediction is supplied by this workspace; the UI displays “Unavailable” when there is no genuine record.
- Dwell/check-in metrics are displayed only when present in configured site/session records.
- A new alert/broadcast, assignment, or dispatch-request action persists a database record. It **does not** call ambulance/police services, place a phone call, send SMS/push, update public displays, or prove message reach. The API returns `external_action_sent: false` / pending-integration statuses. Authorized providers must be separately integrated and tested before operational use.

## Optional keyed mobile-ingestion contract

No mobile application source is present here. If an authorized YatraFlow service is later connected, it can use the following server-side contract. Configure a high-entropy `YATRAFLOW_MOBILE_API_KEY` in the service environment; clients send it as `X-YatraFlow-Integration-Key` over HTTPS. The secret is never placed in frontend JavaScript.

- `POST /api/integrations/mobile/visitors` — register/update a visitor and optionally write a check-in session (`external_id`, `site_id`, `full_name`, optional `phone`, `zone_name`, `session_id`, `checked_in_at`, `checked_out_at`).
- `POST /api/integrations/mobile/sos` — submit an SOS (`site_id`, `external_id`, optional `visitor_id`, `visitor_name`, `zone_name`, `description`, GPS `latitude`/`longitude`, `created_at`). The initial status is `Submitted`.
- `POST /api/integrations/mobile/reports` — submit a visitor report using the same incident fields.
- `POST /api/integrations/mobile/crowd` — append a timestamped zone count; configured zone capacity is required. Density is calculated as count/capacity.
- `GET /api/integrations/mobile/notifications?site_id=1&visitor_id=<id>` — retrieve stored messages for polling. The API resolves the visitor's current zone from the stored profile/session rather than trusting a client-supplied zone. All-site and matching `zone:<name>` audience records may be returned; staff-only records are excluded. This is **polling**, not push delivery.
- `POST /api/integrations/mobile/notifications/{notification_id}/ack` with `visitor_id` — persist an idempotent acknowledgement only if that visitor is eligible for the all-site or matching-zone message; staff-only and other-zone records are rejected.

Treat this contract as an integration starting point. Validate mappings, legal basis, consent, retention, deduplication, rate limits, service identity, provider callbacks and production schema with the YatraFlow owners before connecting real visitor data. Store GPS/contact data only when necessary and restrict it to authorized operational roles.

## Dashboard and API routes

After administrator sign-in:

- `GET /api/session`, `GET /api/sites`, `GET /api/dashboard?site_id=1`
- `GET /api/zones`, `GET /api/zones/{zone_id}`
- `GET /api/incidents`, `GET /api/incidents/{id}`
- `GET /api/visitors?q=...&status=...&filter=all|assistance|vip&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&page=1&page_size=25` — assistance includes flagged visitor status or a linked nonterminal incident; summary counts are site-scoped.
- `GET /api/analytics?days=14|30|90&aggregation=daily|weekly|monthly`
- `GET /api/alerts`, `GET /api/admins`, `GET /api/broadcasts`, `GET /api/audit`
- `POST /api/login`, `POST /api/logout`
- `POST /api/incidents/{id}/transition|assign|dispatch|call|notes`
- `POST /api/visitors/{id}/checkout`
- `POST /api/broadcasts`, `POST /api/admins`, `POST /api/admins/{id}/update`, `POST /api/alerts/notify`

Incident state transitions are checked against the SOS/report lifecycle, recorded with status history and audit events, and reject stale state updates. Personal contact/location fields are only returned to authorized operational roles. Browser write requests are same-origin checked.

## Tests

Run unit and API verification tests with:

```bash
python3 -m unittest discover -s tests -v
```

The API test suite uses a temporary SQLite database and does not write to `data/yatraflow.sqlite3`.
