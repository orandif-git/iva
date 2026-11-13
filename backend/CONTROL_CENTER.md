# Jas iVision Control Center

The console Jas iVision's own staff use to set up and look after every customer. It lives at `/console` (static files in `web/console/`, no build step) and signs in the same
way as Platform Admin: sign in at `/`, a staff account is handed on. It talks only to the admin API (`/admin/v1`).

## Who can do what

| | Owner | Admin | Onboarding | Support |
|---|---|---|---|---|
| See customers, messages, log | yes | yes | yes | yes |
| Create a customer, recorders, RTSP cameras | yes | yes | yes | no |
| People and opening hours of a shop | yes | yes | yes | yes |
| Events, cameras and marked areas of a shop | yes | yes | yes | no |
| Pause or resume a customer | yes | yes | no | yes |
| Change the end date | yes | yes | no | no |
| WhatsApp templates (webhooks), console users | yes | yes | no | no |
| Delete a customer, WhatsApp connection and key | yes | no | no | no |

`admin_ctx("owner")` lets in Admin as well; `admin_ctx("owner!")` is the Owner alone. An Admin cannot create or change an Owner.

## What was added to the backend

* `migrations/020_control_center.sql`: roles `admin` and `onboarding`; `app.tenants.ends_on`, `day_hours`, `msg_language`; `app.alert_rules.label`.
* `routers/admin_shop.py`: `/admin/v1/tenants/{id}/shop/...` hours (a different time on each day), people, events, areas, cameras, channels, snapshot, test message.
  It runs the customer's own code with row-level security set to that one customer, and every change is audited with the staff member's name (`staff:<role>`).
* `services/service_end.py`: every 15 minutes, a customer whose end date has passed is told once (alert `service_ended`) and suspended. Extend the date and reactivate to bring them back.
* `DELETE /admin/v1/tenants/{id}` (Owner): removes a customer and everything of theirs.
* `services/opening_hours.py` understands a different time on each day (`Hours.per_day`); the single open and close times stay filled for everything that reads them.

## What an "event" is

An event is an alert switched on for one shop with its settings and the people chosen (`app.alert_rules`, with a name in `label`). Area events (people in an area, long queue,
possible phone use) also save the marked area on the chosen camera. The camera of a non-area event is not stored: those alerts are per shop.
