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

## Adding an event (the wizard)

Event (name and who it goes to) → Event type (the full list, grouped) → Recorder and Camera only when the type needs a camera → Mark the area (area types only) → Settings.
A system event is three steps. Types that need a camera: marked-area events, Count people, Read number plates, and the alerts that depend on them (crowding, after-hours, counting stopped, blocked vehicle, vehicle overstay, unclear plate).

The Events list shows everything the shop really gets: the alerts that are on by default (tagged Default), the others that were added, one row for each marked area (with its camera) and one for each camera that counts people or reads plates.
Edit opens a side panel; Delete means "this shop stops getting it" and changes this shop only. An alert that is on by default is kept as switched off so the default does not bring it back.

## Activity in an area

A new kind of marked area (`activity_area`, alert `area_activity`). It looks for people or for vehicles (`target`) in the area, at the time set: while the shop is closed (needs opening hours), between two times
(a night across midnight is fine), or at any time (`when`). It is drawn only in the Control Center; a customer's own app does not know this kind.
The camera's worker counts vehicles in the area even when the camera reads no plates. The backend decides whether the time is one that is watched.
Run `migrations/021_activity_area.sql` before using it. Other kinds of thing the detector can recognise (bags, animals and so on) can be added to `target` later.

## Marking on the picture

Every event that needs a camera needs something marked on that camera's picture: an area (area events), a counting line with its IN direction (Count people, and the alerts that depend on counting), or the area plates are read in (Read number plates, and the plate alerts; leaving it out means the whole picture).
Without a counting line the worker counts nobody, so a camera that "counts people" but has no line is shown in the Events list with "No line marked. Nothing is counted yet". The line and the plate area are saved through `/admin/v1/tenants/{id}/shop/cameras/{camera}/zones`.

## How long data is kept

One setting for every customer (Setup, Data kept; `platform.data_retention`, default 30 days, 7 to 730): alerts and their messages, people counts, plate reads, and pictures and clips.
Saving it writes the same number into every customer's own retention and into the expiry of stored pictures, so the existing clean-up (every six hours, `services/retention.py`) does the work.
New customers start with the current setting. An alert that is still being sent is never removed. Clear now runs the clean-up straight away and is recorded in the Activity log.

## The message log

`GET /admin/v1/messaging/log` reads the log for a range of days (India time), a status group and a search, 100 at a time with the total; both Messages pages use it.

## Videos on area alerts

People in an area, long queue, activity in an area and phone use can now send a short video, the same way a blocked vehicle does.

- A video is made only when the customer's account allows videos and the alert's `clip` setting is not off.
- It covers about 10 seconds before and 5 seconds after the latest report, at 5 pictures a second. The seconds before are sparser when nothing was in view.
- The shop's date and time are written in the bottom-left corner of every picture of the video.
- Three shortcodes give the time range on the shop's clock, so the owner can find the same moment on the recorder: `{clip_time}` (13 Nov, 10:11:50 PM to 10:12:05 PM), `{clip_from}` and `{clip_to}`.
- The message waits up to 40 seconds for the video, then goes out without it. The webhook template must contain `{clip_url}` for the message to wait.

## Pictures for occupancy and after-hours alerts

These two pictures are taken by the API (not the worker). Before they are saved, the AI draws a green box on each person, the counting line with its IN arrow, and a caption with the number of people in view. If the detector model is not available, the plain picture is used and the alert is not delayed. The detector is the same model file the worker uses (`GI_WORKER_MODEL_DIR`), so the API must be able to read it.

## Changing a recorder

On Recorder and cameras, each recorder has an **Edit** button (Owner, Admin and Onboarding; Support can only look).
- **Name and Recorder ID** can be changed. The ID is what the recorder calls itself when it connects; after a change it shows as "not connected" until the new ID is typed into the recorder. The saved password is re-locked to the new ID, so it keeps working.
- **New password** makes a new password, shown once. The old one stops working at once.
- **Delete** (type the recorder's name to confirm) removes the recorder, its cameras with their counts and marked areas, and any event left with nothing to watch. Alerts and messages stay until the "Data kept" clean-up removes them.

## Customer and owner details

New customer, step 1 ("Customer"): customer name, owner name, owner mobile (up to three numbers), owner email (optional) and city. Name, owner name, the first mobile and city are required. The state, GSTIN and billing address are not kept here, and neither is the message language (it did nothing).
- An owner mobile can belong to one customer only (it will be used later to sign in with a one-time code). A number already used is refused with the name of that customer. People who get messages can share numbers freely.
- Numbers are stored as +91XXXXXXXXXX, so "98765 43210" and "+91 98765 43210" are the same number. The activity log keeps only the last four digits.
- The owner starts as the first person who gets messages.
- **Edit** on the customer page changes all of these (Owner, Admin and Onboarding). Onboarding cannot change the end date or other account settings.
- Customers made before this have no owner mobile; their page shows a note until it is added with Edit.

## Changing and deleting console users

Setup, Console users: **Edit** changes a person's name, email and role. **Delete** removes them for good (type their name to confirm). Owner and Admin only; an Admin cannot touch an Owner.
- You cannot change your own role or delete yourself, and the last active Owner can never be changed or deleted.
- A changed role signs the person out, so the old permissions do not linger.
- The record of which customers a person viewed ("view as customer") stays after they are deleted, with their name.

## "View as customer" removed

There is no customer app any more, so staff can no longer open a customer's account on screen. The endpoints and the button in the old Platform Admin console are gone. The saved records of earlier views stay in the database (with the staff member's name) and old activity-log lines still read normally.

## Plate events: one setup, in and out

**Read number plates** is the one place that sets up a plate camera: the camera, the plate area and **which way is IN** (the way a vehicle moves in the picture when it comes in: left, right, away from or towards the camera). The wizard will not go on until the direction is chosen. No corners drawn means the whole picture. The direction is saved with the plate area, once per camera.
- **Vehicle overstay**, **Blocked vehicle** and **Number plate needs review** no longer ask for a camera or an area. They work on every camera that reads plates. If none does, the wizard says so and offers "Set up Read number plates".
- Overstay needs the direction (it only looks at vehicles that came in); a plate camera without one shows "No direction" in the Events list, and the Overstay row shows "No direction on N cameras". Nothing is changed or deleted behind anyone's back: open the Read number plates row, press "Mark the plate area and direction".
- An exit is matched to the latest unmatched entry of the same plate at any of the shop's plate cameras, up to 7 days back.
- The Vehicle overstay message has a new shortcode, `{entered_at}` (when the vehicle came in, on the shop's clock), next to `{plate}`, `{minutes}`, `{hours}` and `{stay}`. Add it to the overstay webhook payload if the WhatsApp template should show it.

## People count and Vehicles

Two read-only pages under each customer, in the menu after Events. A page shows when the matching event is switched on (a camera counts people / reads plates) **or** the customer still has saved history of it. With the event off and no history left, it hides by itself (history is removed after the "Data kept" days). With the event off but history kept, the page shows a note saying nothing new is being counted.
- **People count**: Today / Yesterday / 7 / 30 days, all doors or one. People in, out, the busiest hour (or day), "about inside now" (an estimate from today's counts, shown only when today is in the range), a chart of people per hour or per day, and a per-camera table. Days are the shop's own days.
- **Vehicles**: plate reads newest first with the picture (vehicle and plate boxed), plate search, range, in or out, "Blocked list" and "Needs review". An entry shows how long the vehicle stayed once an exit of the same plate was read at any of the shop's plate cameras; with no exit it shows "Inside now?" (it may have left unseen). Click a row for a larger picture. "Load more" pages through the list.
- **Export to Excel** on both pages, with the same builders as the scheduled reports (vehicles: the gate register; people: the footfall summary).
- Endpoints (staff, all roles can view): `GET /admin/v1/tenants/{id}/shop/views`, `/people-count`, `/people-count/export`, `/vehicles`, `/vehicles/export`. Pictures are short signed links (1 hour) and are offered only while the picture is kept.

## Opening a customer's sections is quick

What a customer's page shows is loaded once and kept. Opening another section reuses it and refreshes it quietly in the background (at most every 8 seconds), updating the page only when something it shows has changed and never while a window is open. The marked areas and plate/line data of every camera are fetched only when the Events list (or the event wizard) needs them. The menu remembers whether People count and Vehicles apply to the customer, so it no longer flickers. Saving a change still reloads the customer's data.

## My profile

Click your avatar at the top right: a compact panel drops down (the light/dark switch stays as its own icon next to it).
- **Name**: the pencil edits it in place. Your email and role are shown; an Owner or Admin changes those under Setup, Console users.
- **Change password**: needs the current one; the same rules as sign-in (10 or more characters, nothing common or easy). Wrong tries count like wrong sign-ins and are logged. Every other device is signed out; this one stays.
- **Sign out everywhere else**: shows how many devices you are signed in on and ends the others after a yes.
- **Sign out** is at the bottom of the panel.
- There is no authenticator set-up in the profile. Endpoints: `GET/PATCH /admin/v1/me`, `POST /admin/v1/me/change-password`, `POST /admin/v1/me/sign-out-others`. The activity log records that a change happened, never a password.

## System health

Setup, System health (Owner and Admin). One page for our own system, read only: a verdict at the top ("Everything is healthy" or the list of things that need attention, each with what to do),
the services (API, analysis worker, Dahua gateway, registrar, database) and when each last reported in, the server's processor, memory and disk (with "about N days left at this pace"),
the camera pipeline (cameras analysed against cameras expected, and any camera that sent nothing for 10 minutes), WhatsApp messages in the last 24 hours (waiting, failed, the oldest one stuck),
storage and the biggest database tables, a rough "more cameras fit" estimate, and three 24-hour charts. The page refreshes itself every 30 seconds.

- Endpoint: `GET /admin/v1/system/health` (`routers/admin_system.py`, `services/system_health.py`). Amber starts at 80% disk, 85% memory, 70% processor; red at 90%, 95%, 90%.
- Every 5 minutes the API saves one reading in `platform.health_samples` (migration 027), kept 8 days. The charts and the disk estimate come from these, so they fill in after the first hours.
- The gateway and registrar are checked by connecting to their local video ports (`GI_DAHUA_RELAY_URL`, `GI_GB_RELAY_URL`). The worker is "running" when it asked for its cameras in the last 2.5 minutes.
- Processor is the server's load divided by its processors, so it is right when the worker runs on the same machine as the API (the case today).

## Access key on alerts

Each alert (Setup, WhatsApp templates) sends to its own address and needs the provider's key. Open the alert, then "Access key": paste the key, or press "Use the WhatsApp connection key" (Owner) when it is the same key as the summaries use. Ticked "Use it for every alert with this address", it is saved on all alerts that send to the same address in one go. The key goes out as `Authorization: Bearer <key>`; other headers set earlier are kept.

- Endpoint: `POST /admin/v1/webhooks/key` (`endpoint_id`, `key` or `use_connection`, `all_on_address`). The screen only ever learns the last 4 characters (`key_hint`), never the key; the old `headers` field of the alert list was replaced by `header_names` and `key_hint`.
