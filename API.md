# Jas iVision API routes

Four separate APIs on the same backend. Each has its own base path, its own token audience and its own roles, so a token for one can never work on another.

| API | Base path | Who calls it | Token |
|---|---|---|---|
| Customer | `/v1` | Customer web app | Tenant user JWT (`aud=customer`, claims: `tenant_id`, `role`) |
| Platform admin | `/admin/v1` | Your super admin console | Staff JWT (`aud=platform`, claims: `staff_id`, `role`, `mfa=true`) |
| Partner | `/partner/v1` | Installer and reseller portal | Partner JWT (`aud=partner`, claim: `partner_id`) |
| Internal | `/internal/v1` | Registrar and AI workers | mTLS plus service token, never exposed to the internet |

## Conventions

- **Tenant comes from the token only.** Never read a tenant id from the URL or body in the customer API. The request runs in a transaction with `SET LOCAL ROLE app_user` and `set_config('app.tenant_id', <claim>, true)`, so row-level security enforces isolation even if a query forgets a filter.
- **Errors:** `{ "error": { "code": "camera_limit_reached", "message": "...", "request_id": "..." } }` with the right HTTP status (400, 401, 403, 404, 409, 422, 429).
- **Lists:** cursor pagination. `?limit=50&cursor=...` returns `{ "items": [...], "next_cursor": "..." | null }`.
- **Times:** ISO 8601 UTC in the API. The UI shows the site's timezone.
- **Idempotency:** `POST` routes that create things or send messages accept an `Idempotency-Key` header and return the first result on a retry.
- **Rate limits:** per token and per IP. Stricter on login and on recorder credential routes.
- **Every write** by staff, partners or an impersonation session writes a row to `platform.audit_log` in the same transaction.
- **Impersonation tokens** carry `imp_session_id` and `read_only=true`. The customer API opens their transactions with `SET TRANSACTION READ ONLY`, so even a bug cannot write.

---

## 1. Customer API (`/v1`)

Roles: `A` = tenant admin, `R` = regular user (both unless noted).

### Session and account
| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/v1/auth/login` | public | `{ email, password, tenant? }` → `{ access_token (15 min), refresh_token, expires_in, user }`. `tenant` (the organisation's short name) is needed only when the email belongs to several organisations (the answer is then `409 choose_account`). Every failure is the same `401 wrong_credentials`. Five wrong passwords lock the account for 15 minutes (`429 too_many_attempts`); attempts per address and per email are limited too. |
| POST | `/v1/auth/refresh` | public | `{ refresh_token }` → a new pair. Each refresh token works once; presenting an old one again ends that whole sign-in. |
| POST | `/v1/auth/logout` | public | `{ refresh_token }` → 204. Ends that sign-in. |
| POST | `/v1/auth/set-password` | public | `{ token, password }` from an invitation or reset link (works once) → 204. Password: 10+ characters, not a common one. |
| POST | `/v1/auth/change-password` | A R | `{ current_password, new_password }` → 204. Other devices are signed out. |
| GET | `/v1/me` | A R | Current user, tenant, plan, limits |
| GET | `/v1/tenant` | A R | Organisation details, plan, camera usage |
| GET | `/v1/users` | A | List users |
| POST | `/v1/users` | A | Invite a user (email, phone, role) |
| PATCH | `/v1/users/{id}` | A | Change role, phone, status |
| DELETE | `/v1/users/{id}` | A | Disable a user |
| GET, POST, PATCH, DELETE | `/v1/sites`, `/v1/sites/{id}` | A (write), R (read) | Sites |

### Recorders (the registration flow)
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/recorders` | A R | List recorders with status |
| POST | `/v1/recorders` | A | Start onboarding: body `{ brand: "dahua", protocol?: "auto_register", site_id?, name? }`. Customers can add Dahua recorders only: any other brand or `gb28181` is refused with 403 `staff_only` (staff add those, see `POST /admin/v1/tenants/{id}/recorders`), and Hikvision or `isup` with 422 `not_supported`. Returns `{ recorder_id, server, port, device_id, key, viewer_user }`. **The key is returned once.** |
| GET | `/v1/recorders/{id}` | A R | Detail and status |
| GET | `/v1/recorders/{id}/status` | A | Poll for the connect screen: `{ status, checks: [reached, key_ok, channels_found, preview_ok] }` |
| GET | `/v1/recorders/{id}/events` | A | Same as above as server-sent events, so the page updates itself |
| GET | `/v1/recorders/{id}/channels` | A | Channels found, each with a snapshot URL |
| POST | `/v1/recorders/{id}/rotate-key` | A | New key, shown once |
| PATCH | `/v1/recorders/{id}` | A | Rename: `{ name }` (null clears it) |
| GET | `/v1/recorders/{id}/connection` | A | The non-secret connection details (server, port, device id, viewer user); never the key |
| DELETE | `/v1/recorders/{id}` | A | Remove the recorder, its cameras and those cameras' counts and plate reads (permanent) |

### Cameras and zones
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/cameras` | A R | List with status and last frame time |
| POST | `/v1/cameras` | A | Enable a channel: `{ recorder_id, channel_no, name, use_footfall, use_anpr }`. Fails with `camera_limit_reached` at the plan limit. |
| PATCH | `/v1/cameras/{id}` | A | Rename, change use cases, pause |
| DELETE | `/v1/cameras/{id}` | A | Remove |
| GET | `/v1/cameras/{id}/snapshot` | A R | Latest frame (short-lived signed URL) |
| GET | `/v1/cameras/{id}/zones` | A R | Counting line and ANPR zone |
| PUT | `/v1/cameras/{id}/zones` | A | Replace zones: `[{ kind, points, in_direction }]` |

### Footfall
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/footfall` | A R | `?from&to&camera_id&interval=hour\|day&tz=Asia/Kolkata` returns `{ items: [{ bucket, people_in, people_out }] }` with empty buckets filled as zero. Days are cut at midnight in `tz`. |
| GET | `/v1/footfall/summary` | A R | Totals, peak period, inside-now estimate |
| GET | `/v1/footfall/live` | A R | *Not built yet.* Server-sent events with the running count. Poll `/v1/footfall` meanwhile. |

### Vehicles (plates)
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/plates` | A R | `?from&to&q&camera_id&direction&review_status&cursor` |
| GET | `/v1/plates/{id}` | A R | Detail with matched entry and dwell time |
| GET | `/v1/plates/{id}/snapshot` | A R | *Not built yet* (needs object storage). Reads carry `has_snapshot` and the worker's `snapshot_key`. |
| PATCH | `/v1/plates/{id}` | A | Confirm or correct a plate: `{ plate_text }` sets `review_status` to `corrected` |
| GET | `/v1/plates/review-queue` | A | Low-confidence reads waiting for review |
| GET, POST, DELETE | `/v1/vehicle-lists`, `/v1/vehicle-lists/{id}` | A (write), R (read) | Blocked and allowed plates (must be complete, valid plates) |

### Alerts and reports
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/alerts` | A R | Recent alerts |
| GET | `/v1/alerts/{id}/image` | A R | The picture kept with an alert, for people signed in to that customer. `404 picture_not_found` once it has passed the retention period. |
| POST | `/v1/alerts/{id}/evidence-link` | A R | `{ url, expires_in_minutes, has_image, has_clip }`: a link to the page showing the alert's picture and video, good for 60 minutes. Every one is written to the audit log. |
| GET | `/v1/alerts/summary` | A R | `?from&to` (at most 62 days): how many alerts of each kind happened, and where (`rule_type`, `place`, `count`, `last_at`). The numbers behind the Activity screens. |
| POST | `/v1/alerts/{id}/ack` | A R | Acknowledge |
| GET, POST, PATCH, DELETE | `/v1/alert-rules`, `/v1/alert-rules/{id}` | A | Account-wide defaults (kept for older apps) |
| GET, POST, PATCH, DELETE | `/v1/rules`, `/v1/rules/{id}` | A | The rules a customer built: `{ name, rule_type, camera_id?, zone_id?, condition, enabled, channels, recipient_ids, quiet_start?, quiet_end?, version? }`. Several of one kind are allowed. The most specific rule wins: a marked area beats its camera, a camera beats the account-wide default. A disabled rule *suppresses* alerts at its level instead of falling back. `PATCH` with a stale `version` answers `409 rule_changed`. |
| POST | `/v1/rules/{id}/test` | A | Send one test message through this rule |
| GET | `/v1/rules/{id}/deliveries` | A | Recent deliveries for this rule |
| GET | `/v1/notifications` | A | Delivery log (sent, delivered, failed) |
| GET | `/v1/reports` | A R | Generated reports |
| POST | `/v1/reports` | A | Generate now: `{ report_type, from, to }`. Returns `202` and a report id. |
| GET | `/v1/reports/{id}/download` | A R | Signed URL for the Excel file |
| GET, POST, PATCH, DELETE | `/v1/report-schedules`, `/v1/report-schedules/{id}` | A | Schedules |

### Billing and transparency
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/v1/billing/plan` | A | Plan, price per camera, next invoice estimate |
| GET | `/v1/billing/invoices` | A | Invoices and status |
| GET | `/v1/access-log` | A | Every time your staff viewed this account, with the reason |

---

## 2. Platform admin API (`/admin/v1`)

> **Platform console (current).** Staff have two roles, **owner** (everything) and **support** (look, help, send a customer a sign-in link, open a customer's app with a logged reason, read the delivery log). The tables below that mention technician, finance or the single-letter role codes are older; where they disagree, this box wins.
>
> | Method | Path | Who | Purpose |
> |---|---|---|---|
> | GET | `/admin/v1/plans?include_retired` | all | Plans with `customers` and `customers_on_old_price` counts |
> | POST, PUT, DELETE | `/admin/v1/plans`, `/admin/v1/plans/{id}` | owner | Create, edit (price, speed, `anpr`, `review_queue`, `price_change_policy` of `next` or `keep`, `active`), delete (only a plan with no customers). A reason is required. With `keep`, customers already on the plan keep their old price (saved as `price_override_paise`); with `next` they move to the new price from the next invoice. |
> | GET | `/admin/v1/overview/attention` | all | What needs a person: offline recorders, recorders never connected for a day, overdue invoices (owner), trials ending, failed messages |
> | GET | `/admin/v1/tenants/{id}/health` | all | Cameras streaming, last picture, last sign-in, alerts and failed messages in 7 days |
> | PATCH, DELETE | `/admin/v1/tenants/{id}/users/{uid}` | owner | Edit a person (name, email, phone, role) or delete them (`?reason=`). The last administrator cannot be removed or demoted. |
> | POST | `/admin/v1/tenants/{id}/users/{uid}/enable` | owner | Enable a disabled person. `disable` and `reset-link` take an optional `?reason=`. |
> | GET, POST | `/admin/v1/staff` | owner | List staff; add one (returns a one-time `/staff/set-password?token=` link) |
> | PATCH | `/admin/v1/staff/{id}` | owner | Name or role. There is always at least one active owner, and nobody changes their own role. |
> | POST | `/admin/v1/staff/{id}/disable`, `/enable`, `/reset-link` | owner | `?reason=` required |
> | POST | `/admin/v1/auth/set-password` | public | `{ token, password }` from a staff invitation or reset link |
> | GET | `/admin/v1/audit` | owner, support | `?q&since&until&action&tenant_id&actor_id&cursor` |
>
> | GET, PUT | `/admin/v1/storage` | owner | Where evidence is kept: `{ backend: local or r2, account_id, bucket, endpoint_url, access_key_id, secret_access_key, reason }`. The secret is encrypted and never returned. A change is saved only after a test file is written, read back and deleted. |
> | POST | `/admin/v1/storage/test` | owner | Try settings without saving; a blank secret uses the saved one |
> | GET | `/v1/plates/{id}/image` | customer | The picture taken when the plate was read (404 when none, or after the retention period) |
>
> | POST | `/admin/v1/cameras/{id}/ai-view` | owner, support | Open or keep open "what the AI sees" for a camera (call every ~10 s while it is on screen; it switches itself off 45 s after the last call) |
> | GET | `/admin/v1/cameras/{id}/ai-view/image` | owner, support | The latest annotated picture (boxes, counting line, running count); 404 until the worker has sent one. Never stored beyond a few minutes. |
> | GET, POST | `/internal/v1/cameras/{id}/ai-view`, `/ai-frame` | worker | Is anyone looking? / the annotated JPEG (accepted only while someone is) |
>
> | POST | `/v1/auth/sign-in` | public | **One sign-in for everyone.** `{ email, password, tenant?, code? }`. Staff are checked first, then customers. Answers `{ kind: "staff" \| "customer", access_token, refresh_token, ... }`, or `{ next: "code" }` when a staff member has the right password and still needs their authenticator code. A wrong password always gets the same plain answer. |
>
> Customers connect their own Dahua recorders, rename them and remove them. Staff can list recorders, read their connection details, and add the types customers cannot (GB28181 recorders and the "other" brand) with `POST /admin/v1/tenants/{id}/recorders`. Hikvision is not supported.


Roles: `O` Owner, `S` Support, `T` Technician, `F` Finance, `N` read-only analyst. Every route also requires `mfa=true` in the token.

### Session
| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/admin/v1/auth/sso/callback` | public | Sign in through your identity provider |
| POST | `/admin/v1/auth/mfa/verify` | public | Second factor |
| POST | `/admin/v1/auth/login` | public | `{ email, password, code }`, where `code` is the 6-digit code from the staff member's authenticator app. Same tokens as above (access 15 min, refresh 12 h); a code cannot be used twice. |
| POST | `/admin/v1/auth/refresh`, `/admin/v1/auth/logout` | public | As for customers. |
| GET | `/admin/v1/tenants/{id}/users` | all | A customer's people (no secrets) |
| POST | `/admin/v1/tenants/{id}/users` | owner, technician, support | `{ email, name, role, phone? }` → creates an invited account and returns a one-time `link` (valid 7 days) to send to the person. |
| POST | `/admin/v1/tenants/{id}/users/{uid}/reset-link` | owner, technician, support | A one-time link (valid 1 hour) to set a new password; a new link cancels the old one. |
| POST | `/admin/v1/tenants/{id}/users/{uid}/disable` | owner, support | Disables the user and signs them out at once. |
| GET | `/admin/v1/me` | all | Current staff user and permissions |

### Overview and health
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/admin/v1/overview` | all | Customers, cameras, recorders, revenue, failed deliveries |
| GET | `/admin/v1/overview/attention` | all | Offline recorders, overdue payments, trials ending, recorders never connected |
| GET | `/admin/v1/health` | all | Registrar, media relay, workers, queue, database, WhatsApp status |
| GET | `/admin/v1/metrics/frames` | all | Frames analysed per hour |
| GET | `/admin/v1/search` | all | `?q&limit` (1–20, default 6): customers and recorders by name, city, device id or customer. Pages are matched in the console itself from `routes.js`. |
| GET | `/admin/v1/events` | all | Server-sent events, one connection per console tab. `event: counts` with `{kind, alerts_open, recorders_offline, recorders_never, trials, customers}` whenever a number changes; keep-alive comments otherwise. Read with `fetch` (the token is a header, never a URL); the browser reconnects every 5 s. A proxy must not buffer this response (`X-Accel-Buffering: no`). |

### Tenants
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/admin/v1/tenants` | all | `?status&partner_id&q&cursor` |
| POST | `/admin/v1/tenants` | O T | Create with a trial: `{ name, city, partner_id, plan_id }` |
| GET | `/admin/v1/tenants/{id}` | all | Detail |
| PATCH | `/admin/v1/tenants/{id}` | O T | Edit limits, retention, partner |
| POST | `/admin/v1/tenants/{id}/suspend` | O | `{ reason }` required |
| POST | `/admin/v1/tenants/{id}/reactivate` | O | `{ reason }` required |
| POST | `/admin/v1/tenants/{id}/plan` | O F | `{ plan_id, reason }` |
| GET | `/admin/v1/tenants/{id}/frame-rate` | all | The tenant's frame-rate policy: `{ mode, idle_fps, active_fps, fixed_fps }`. Default is adaptive, 2 and 5. |
| PUT | `/admin/v1/tenants/{id}/frame-rate` | O F | Change any of `mode` (`adaptive` or `fixed`), `idle_fps` (1-10), `active_fps` (1-25), `fixed_fps` (1-25), with a `reason`. Idle cannot exceed active. Applies to every camera that does not override it. |
| GET | `/admin/v1/tenants/{id}/cameras` | all | Every camera with its overrides, the rate it really runs at (and where each part comes from), live health (`stream_state`, `analysis_fps`, `frames_dropped`) and whether a stream address is set. |
| PUT | `/admin/v1/cameras/{id}/frame-rate` | O T | Per-camera override of any part of the policy. Parts you leave out keep their value. |
| DELETE | `/admin/v1/cameras/{id}/frame-rate?reason=` | O T | Remove all overrides, so the camera follows its tenant again. |
| PUT | `/admin/v1/cameras/{id}/source` | O T | Set the RTSP address the worker pulls (`{ url, reason }`). Stored encrypted; never returned, logged or put in the audit log; only the host is shown. |
| DELETE | `/admin/v1/cameras/{id}/source?reason=` | O T | Clear it. |
| GET | `/admin/v1/tenants/{id}/recorders` | all | Their recorders |
| GET | `/admin/v1/tenants/{id}/users` | O S T | Their users |
| GET | `/admin/v1/tenants/{id}/usage` | O F | Frames, alerts, storage, cost, margin |
| GET | `/admin/v1/tenants/{id}/audit` | O S | Actions on this tenant |

### Recorders
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/admin/v1/recorders` | all | `?status&tenant_id&q` |
| POST | `/admin/v1/tenants/{id}/recorders` | O | `{ brand, protocol?, site_id?, name?, reason }`. Adds a recorder for a tenant, including GB28181 and the "other" brand, which customers cannot add. Hikvision and `isup` are refused (422 `not_supported`). Returns server, port, device id and key once (`no-store`); the key is never written to the audit log. |
| POST | `/admin/v1/recorders/{id}/rotate-key` | O T | `{ reason }`. New key shown once. |
| POST | `/admin/v1/recorders/{id}/restart-stream` | O T S | Ask the registrar to restart the stream |
| DELETE | `/admin/v1/recorders/{id}` | O | `{ reason }` |

### View as customer
| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/admin/v1/tenants/{id}/impersonate` | O S | `{ reason, minutes (15-60), notify }`. Creates a session and returns a read-only customer token. |
| DELETE | `/admin/v1/impersonations/{session_id}` | O S | End early |
| GET | `/admin/v1/impersonations` | O | Active and past sessions |

### Partners, billing, plans
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET, POST | `/admin/v1/partners` | O F | List and invite |
| GET, PATCH | `/admin/v1/partners/{id}` | O F | Detail, commission rate |
| GET | `/admin/v1/partners/{id}/statement` | O F | `?month=2026-11` customers, revenue and commission payable |
| GET | `/admin/v1/billing/summary` | O F | Revenue, cost, margin, overdue |
| GET | `/admin/v1/billing/invoices` | O F | `?status&tenant_id` |
| POST | `/admin/v1/billing/invoices/{id}/retry` | O F | Retry a failed payment |
| GET, PUT | `/admin/v1/plans`, `/admin/v1/plans/{id}` | O (write), O F (read) | Plans and prices |

### Staff and audit
| Method | Route | Role | Purpose |
|---|---|---|---|
| GET | `/admin/v1/staff` | O | Staff list with MFA status |
| POST | `/admin/v1/staff` | O | Invite, with role |
| PATCH | `/admin/v1/staff/{id}` | O | Change role |
| POST | `/admin/v1/staff/{id}/disable` | O | Disable and revoke sessions |
| GET | `/admin/v1/audit` | O S | `?action&tenant_id&actor_id&from&to&cursor`. Read-only. |
| GET | `/admin/v1/audit/export` | O | CSV export (this export is itself audited) |

There is no `PATCH` or `DELETE` on the audit log, and the database blocks it too.

---

## 3. Partner API (`/partner/v1`)

A partner sees only tenants where `tenants.partner_id` matches their partner.

| Method | Route | Purpose |
|---|---|---|
| GET | `/partner/v1/tenants` | Their customers with status and camera counts |
| POST | `/partner/v1/tenants` | Onboard a new customer (starts a trial) |
| GET | `/partner/v1/tenants/{id}` | Customer detail (no footage, no plates) |
| POST | `/partner/v1/tenants/{id}/recorders` | Register a recorder and get the setup values |
| GET | `/partner/v1/statements` | `?month=` commission statement |

---

## 4. Internal API (`/internal/v1`), service to service only

| Method | Route | Caller | Purpose |
|---|---|---|---|
| POST | `/internal/v1/recorders/{device_id}/heartbeat` | Registrar | `{ ip, channels, firmware }`. Sets status `online`, updates `last_heartbeat_at`. |
| POST | `/internal/v1/recorders/{device_id}/offline` | Registrar | Connection dropped |
| GET | `/internal/v1/recorders/{device_id}/credentials` | Registrar | Decrypts the key with KMS for the protocol handshake. Never logged. |
| GET | `/internal/v1/workers/assignments` | AI worker | Cameras this worker should read, with zones and settings |
| GET | `/internal/v1/cameras/{id}/config` | AI worker | Settings, plate zone and counting line, whether it may send (`active`), and the resolved `frame_rate` (`mode`, `idle_fps`, `active_fps`, `fixed_fps`, `capture_fps`, and which level each part came from). Workers ask again every 30 s, so an admin-console change applies without a restart. |
| GET | `/internal/v1/cameras/{id}/source` | AI worker | Where the video comes from, `no-store`. `{ kind: "rtsp", url }` when an administrator set an address (it holds the camera password and wins), or `{ kind: "gb28181", device_id, channel_no }` for a camera on a recorder that registered with us: the worker then asks the video relay. 404 if neither, 403 if the camera may not be analysed. |
| POST | `/internal/v1/cameras/{id}/frame-status` | AI worker | Health heartbeat every 15 s: `{ state, analysis_fps, dropped, error }`. Passwords are scrubbed from `error`. The answer `{ active }` tells the worker whether to keep going. A camera silent for 60 s is marked offline. |
| POST | `/internal/v1/events/footfall` | AI worker | Batch of up to 1,000 one-minute counts. Safe to retry. Each bad event is reported by index. |
| POST | `/internal/v1/events/plates` | AI worker | Batch of up to 200 plate reads. The server cleans and validates the plate, drops duplicates, pairs exits with entries, and raises blocked-vehicle alerts. |
| POST | `/internal/v1/cameras/{id}/frame-status` | AI worker | Last frame time, error state |
| POST | `/internal/v1/alerts` | Rules engine | Create an alert and queue notifications |

## 4b. Video relay (served by the registrar process, not the backend)

Private network only. It lets an AI worker read a registered recorder's live video as an MPEG program stream, which FFmpeg reads directly.

| Method | Route | Caller | Purpose |
|---|---|---|---|
| POST | `/relay/v1/streams` | AI worker | Bearer service token. `{ device_id, channel_no }`. Returns `201 { url, expires_in }`: a **single-use** play link valid for about a minute. Errors: `404 device_offline / unknown_channel / channel_offline`, `429 too_many_streams / too_many_requests`, `422`, `401`. |
| GET | `/relay/v1/play/{token}` | FFmpeg | The program stream (`video/mp2p`) until the viewer leaves, the recorder stops sending, or the viewer cannot keep up. Starts the recorder's stream if it is not running; viewers share one. `404 link_invalid` if the link was used or expired, `502 device_refused`, `504 device_timeout`. |
| GET | `/relay/v1/streams` | operations | Bearer service token. Running streams: viewers, packets, bytes, packets lost, seconds since the last packet. |
| GET | `/healthz` | monitoring | `{ ok: true }` |

## 5. Webhooks (public, signature verified)

| Method | Route | From | Purpose |
|---|---|---|---|
| POST | `/webhooks/razorpay` | Razorpay | Payment succeeded or failed, subscription events |
| POST | `/webhooks/whatsapp` | WhatsApp provider | Delivery and read status |

---

## Permission summary (matches the admin mockup)

| Action | Owner | Support | Technician | Finance | Analyst |
|---|---|---|---|---|---|
| View tenants and recorders | yes | yes | yes | yes | yes |
| Create tenant | yes | | yes | | |
| Register recorder, rotate key | yes | | yes | | |
| Restart stream | yes | yes | yes | | |
| View as customer | yes | yes | | | |
| Suspend or reactivate | yes | | | | |
| Change plan | yes | | | yes | |
| Billing and usage | yes | | | yes | |
| Staff and roles | yes | | | | |
| Audit log | yes | yes | | | |

## Build order

1. Auth for both audiences, `/admin/v1/tenants`, `/admin/v1/recorders`, audit writes.
2. Internal heartbeat and credentials routes with the registrar.
3. Customer recorders, cameras and zones routes (the onboarding flow).
4. Internal events routes, then footfall and plates queries.
5. Alerts, reports, then billing and webhooks.
6. Impersonation and the partner API.

## 4c. Alerts and messaging (built)

Customer `/v1`: `GET/POST /recipients`, `PATCH/DELETE /recipients/{id}`, `POST /recipients/{id}/test?channel=`, `GET /alert-types`,
`PUT /alert-types/{type}`, `GET /alerts`, `POST /alerts/{id}/acknowledge`, `GET /notifications`.
Admin `/admin/v1/messaging`: `GET/POST /providers`, `PUT/DELETE /providers/{id}`, `POST /providers/{id}/{activate|deactivate|test}`,
`GET/POST /templates`, `POST /templates/install-defaults`, `GET /templates/{id}/submission`, `PUT /templates/{id}/status`, `DELETE /templates/{id}`,
`GET/PUT /tenants/{id}`, `GET /notifications`, `GET /summary`.
Internal: `POST /internal/v1/evidence` (worker upload). Public: `GET /e/{token}`, `GET /e/{token}/image.jpg`, `POST /hooks/messaging/{provider_id}`.
Details and limits: `backend/MESSAGING.md`.

Clips (phase 2): `POST /internal/v1/alerts/{id}/clip` (worker), `GET /e/{token}/clip.mp4`; plate batches answer with `clips: [{index, alert_id, seconds_before, seconds_after}]`; camera config has `clips`; tenant messaging has `clips`; the blocked-vehicle alert has `params.clip`.

## 4d. Customer app (built)
Alerts inbox: `GET /v1/alerts?state=open|snoozed|done` (each alert has `state`, `snoozed_until`), `POST /v1/alerts/{id}/acknowledge` (done), `POST /v1/alerts/{id}/snooze` (`{minutes}`, 5 to 1440, default 60).
`POST /v1/alert-types/{type}/test` (`{recipient_id}`, admin) sends one test message to one person; it is marked as a test and never appears in the inbox.
`GET /v1/health-summary`: `status` (`ok` or `attention`), `issues`, `recorders`, `cameras`, `messages`, `data`, `open_alerts`.
Drawn areas: `PUT /v1/cameras/{id}/areas` takes 3 to 12 corners (0 to 1 of the picture); crossing edges, repeated corners and slivers are refused with 422.
Reports are sent on WhatsApp: `POST /v1/report-schedules` needs recipients with a mobile number (`no_mobile_number`), and the file link is `/e/<token>/report.xlsx`.
Live view (admin roles only): `POST /v1/cameras/{id}/live` (`{mode: "live"|"ai"}`) opens a session; `GET /v1/live/{id}` status; `POST /v1/live/{id}/extend` ("keep watching"); `DELETE /v1/live/{id}`; `GET /v1/live/{id}/frame` the current JPEG (in AI view the boxes and areas are drawn on it); `POST /v1/live/{id}/whep` smooth WebRTC video — the body is an SDP offer and the answer is `application/sdp` (see the go2rtc note below); `GET /v1/live/{id}/stream` moving pictures for a live view (not AI view): a `multipart/x-mixed-replace` stream of JPEGs, about 6 a second (`GI_LIVE_STREAM_FPS`), that ends when the view closes, the person leaves or the camera stops sending. At most `GI_LIVE_STREAM_MAX` (8) run on one server; more get `429 video_busy`. A camera that cannot be started gives an ordinary error (409 `camera_offline`, 503 `video_unavailable`) and the app falls back to `/frame`. AI view returns `409 use_frames`. Behind a proxy, turn response buffering off for this route (the server sends `X-Accel-Buffering: no`); `GET /v1/live-history` lists views.

**Smooth video (go2rtc).** Set `GI_GO2RTC_URL` (for example `http://127.0.0.1:1984`) to switch WebRTC on; empty means the app uses the picture stream only. The backend registers each camera with go2rtc as an `exec:` FFmpeg source that transcodes to H.264 baseline and writes MPEG-TS to stdout (WebRTC cannot carry H.265, and the relay's MPEG-PS needs a demuxer). Every offer re-registers the source because the relay's play link is single-use. go2rtc must be able to reach the relay and the cameras, and its `webrtc.candidates` must name the addresses phones can reach (a private address is not enough across NAT). If go2rtc is down the endpoint answers `503 smooth_video_unavailable` and the app keeps the MJPEG/frame path; nothing in the alerting path depends on it.
Live closes after 120 s (up to 600 s in total), AI view after 30 s (up to 120 s in total). A person has one view at a time and a customer at most two at once. AI view is still drawn picture by picture (about one a second), because the boxes are drawn by the AI worker.
