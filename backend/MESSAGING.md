# Alerts: System Alerts, Smart Alerts and webhooks

> Note: the customer app is gone. Routes below written as `/v1/...` (customer) now exist only as staff routes under `/admin/v1/tenants/{id}/shop/...` (see `routers/admin_shop.py`). Customers receive alerts on WhatsApp only.

## The two families

| Family | What it is | Shown with |
|---|---|---|
| **System Alerts** | Keep the cameras, recorders and account healthy: a recorder or camera goes offline, storage is nearly full, and so on | no icon |
| **Smart Alerts** | Come from the AI on the camera pictures or from rules about the customer's business: a blocked vehicle, too many people, a vehicle that stayed too long | the AI sparkle icon |

The catalogue lives in `app/services/alert_catalog.py`: 24 alerts for customers (every one is built) and two system events that are not customer alerts (*Report ready*,
*Video analysis stopped*). **Critical** alerts (blocked vehicle, recorder offline, after-hours activity, disk failure, recording stopped) are always sent: quiet hours and the
hourly flood guard never hold them. There is **no monthly WhatsApp message limit**. The alerts found by looking at the data on a clock are checked **every minute** unless
Platform Admin sets another time for one of them (Alerts page, "Checked").

| Alert | Family | What makes it fire | Settings the customer can change |
|---|---|---|---|
| Recorder offline | System | No heartbeat for `GI_HEARTBEAT_TIMEOUT_SECONDS`. Said again every 6 hours while it stays down | none |
| Camera offline | System | The worker stopped reporting for a minute. Not said when our own analysis has stopped, nor when the recorder is already reported offline | none |
| Unstable connection | System | The recorder went offline `drops` times in the last hour | `drops` (3; 2 to 20) |
| Service restored | System | A recorder or camera is working again **and the customer was told it was down in the last 7 days** | none |
| Recorder time incorrect | System | The recorder's clock is `minutes_out` or more away from the true time | `minutes_out` (5; 1 to 120) |
| Daily system summary | System | Once a day at `hour`, in the customer's time zone | `hour` (8). **Off until the customer turns it on** |
| Recorder storage low | System | The recorder's own warning that it has run out of room **and is not replacing its oldest video**. A full disk by itself is never an alert: a recorder that records all the time is full by design | none |
| Recorder disk failure | System | A disk reports a fault, or the recorder has no disk | none |
| Recorder disk health low | System | A disk reports `percent` or less of its life left (the recorder must say so) | `percent` (20; 5 to 50). Once a week for a disk |
| Recording retention below target | System | The recorder holds fewer than `days` days of video. **Off until the customer turns it on and chooses the number** | `days` (30; 1 to 365) |
| Recorder restarted | System | The recorder's uptime went back down. The third restart in a day says to check the power supply | none |
| Recorder hardware warning | System | The recorder reports heat at or above `temperature`, a stopped fan, or a failed camera power port | `temperature` (70; 40 to 100) |
| Recording stopped | System | A camera recorded earlier today but nothing in the last 2 hours | none |
| Camera position changed or covered | System | The camera's outline sketch no longer matches its usual view for 30 minutes (or the picture has been flat for 15 minutes). Daylight and night keep separate usual views | none |
| Poor image quality | System | Much less sharp than usual for 2 hours, or almost black for 1 hour while the premises are open | none |
| Counting stopped | System | An online camera that counts people has counted nobody for `hours` while the premises are open. Needs opening hours | `hours` (2; 1 to 12) |
| Occupancy limit exceeded | Smart | People counted inside, **for each place** (site, or recorder), reach `people` | `people` |
| After-hours activity | Smart | People counted at a door while closed, after the first `grace_minutes` after closing. Needs opening hours | `grace_minutes` (30; 0 to 180) |
| Blocked vehicle detected, Vehicle overstay, Number plate needs review | Smart | Number plates (an unclear read within one character of a blocked plate is reported as *Possible blocked vehicle*; allowed vehicles are never reported as overstaying) | see the Smart Alerts section |

How the data behind the new alerts is gathered:

* **Recorder readings.** The Dahua gateway asks each connected recorder every 10 minutes (`GI_DAHUA_HEALTH_INTERVAL`) for its disks, its clock and, per channel, how many
  recordings it holds for the last 2 hours and the last day, and posts them to `/internal/v1/recorders/{id}/health`. What a recorder cannot answer is sent as unknown, and
  unknown never raises an alert. The first reading of each recorder is written to the gateway log. `python -m dahua.probe` shows what the installed Dahua kit can read.
* **Picture checks.** Every 5 minutes the worker measures each camera's picture (brightness, contrast, sharpness and a 64 x 36 outline sketch: `worker/picture.py`) and posts
  it to `/internal/v1/events/pictures`. The backend compares it with that camera's own usual view (`app.camera_picture_state`) and only alerts when something has lasted.
  Nothing about any person is kept.
* **Opening hours** are set by the customer in Settings (`/v1/opening-hours`). With none set, *Counting stopped* and *After-hours activity* never fire.
* **Platform faults.** If one of our own analysis workers stops asking for its cameras, a platform fault is opened (Platform Admin, Maintenance) and the chosen staff are
  told through the `system.platform_fault` webhook. Customers are not told their cameras are offline while it lasts.

## Smart Alerts on marked areas

Three Smart Alerts work on **areas** the customer marks on a camera picture (camera page, "Marked areas"). Each area has a name and **its own limits**, so two
counters can have two limits. The alert (on or off, who is told, quiet hours) is set on the Alerts page as for any other alert. **An event is sent only when the
condition is met**, and the same area is not reported again for `repeat_minutes` (default 15).

| Alert | Area kind | Condition (the customer sets the numbers) | Shortcodes |
|---|---|---|---|
| Area occupancy limit exceeded | `area_count` | The count stays at `limit` or more for `minutes` (every report in that time) | `{area} {people} {limit} {minutes}` |
| Long queue | `queue_area` | The queue stays at `length` or more for `minutes`, **or** the longest wait reaches `wait_minutes` (0 turns the wait off). `staff` (0 to 20) people who work inside the shape are left out | `{area} {length} {limit} {minutes} {wait_minutes}` |
| Possible phone use (staff area) | `staff_area` | A phone has been in use for `seconds` without a break. Comes with a picture | `{area} {seconds}` and `{image_url}` |

* **Staff area.** Anyone standing in it counts as staff, so nobody is identified. The customer must confirm that **staff have been told the area is watched**
  (who confirmed, and when, is stored). The alert says "possible phone use" and names the area, never a person.
* **How the worker measures.** Every ten seconds per area it reports how many people stand in the shape (their floor point, or their middle for a staff area), for
  a queue how long the longest-waiting person has been there, and for a staff area how long a phone has been in use. A phone counts as in use when the detector sees
  a phone in the upper part of the box of a person inside the area; a break of five seconds ends it. A picture is taken once the area's `seconds` is reached.
  The first report waits three seconds so people are confirmed first. A camera with no areas does no area work.
* **Accuracy.** Counting is as good as the people detector (best from an angled or overhead view); a queue where people overlap is under-counted, so the wait is more
  reliable than the length; **a phone is a small object, so expect false alarms and misses**. For cameras with staff areas use the larger model
  (`GI_DETECTOR_MODEL=yolox_s`) and the full-quality stream, and treat the alerts as "possible".
* **Which areas a camera analyses.** Only kinds whose alert is on for the customer (built, on in the catalogue, in their plan or added). Saving an area switches the
  camera's `use_areas` on; removing the last one switches it off (a camera used for nothing else cannot lose its last area while it is enabled).
* **API.** Customer: `GET /v1/area-kinds`, `GET/PUT /v1/cameras/{id}/areas` (the list is replaced; an area sent with its `id` keeps its history). Worker: the camera config
  has `areas`, and reports go to `POST /internal/v1/events/zones`. Reports are kept for 3 days (`app.zone_samples`).
* **Launch.** The three alerts start **switched off in the catalogue** (migration 010 and `schema.sql`). Turn them on in the staff console (Alerts) once they have been
  tried on real footage; a customer sees nothing until then.

Detectors run every five minutes in the API process (`services/maintenance.py`). The connection history (`app.recorder_link_events`) is kept for
14 days. A sign-in device is a short fingerprint of the browser's name plus a label such as "Chrome on Windows"; no address is kept.

Alerts are never priced anywhere in the product. What a customer pays is decided somewhere else.

## Who sees which alert

A customer sees an alert only when **all three** are true:

1. it is **built**;
2. the **platform owner has it switched on** (`PUT /admin/v1/alert-catalog/{type}`, needs a reason, audited);
3. the customer **has it**: their plan includes it, or staff added it to them (`PUT /admin/v1/tenants/{id}/alerts/{type}`).

Anything else is not listed at all, and asking for it by name answers "not found". An alert that is off, or not theirs, also raises nothing.

* **Plans choose their alerts.** `alerts` on `POST/PUT /admin/v1/plans`. A plan with no list (`platform.plans.alert_types` NULL) includes every
  alert that exists, which is how plans behaved before. A new plan with no list gets a sensible set: every built System Alert, people counting,
  and the vehicle alerts only when the plan reads number plates.
* **À la carte.** Staff add one alert to one customer on top of their plan, or take it away again (`app.tenant_alert_grants`). A customer's own
  settings for an alert are kept when it is taken away and come back when it is added again.
* **On from the start.** Every System Alert that exists is on until the customer turns it off. Smart Alerts are off until the customer chooses
  them, except blocked vehicles. An alert reaches people only when the customer has chosen who gets it.

## How an alert travels

```
event (blocked plate, camera offline, ...)
  -> raise_alert()      one transaction: the alert row + one queued event per person with a mobile number     (nothing can be lost)
  -> delivery service   claims queued events (FOR UPDATE SKIP LOCKED), POSTs the webhook, records the answer
  -> your system        turns the event into the WhatsApp message
```

**Alerts go out by WhatsApp only, never by email.** (Scheduled reports are still emailed, through the platform's SMTP server.) Push
notifications for the mobile app come later.

* **Customers** choose which alerts are on, who is told, the alert's own settings, and quiet hours (`GET/PUT /v1/alert-types`, `/v1/recipients`).
  Entering a phone number requires ticking *"this person agreed to receive WhatsApp messages"*; who ticked it and when is stored.
* **The platform owner** sets up the webhooks (below), can switch WhatsApp off for one customer (there is no monthly cap)
  (`/admin/v1/messaging/tenants/{id}`), and sees every event in the delivery log.

## Webhooks (owner only)

Every alert has at most one **endpoint**: a name, an address, optional headers, and a **JSON payload** with `{shortcodes}`. When the alert
happens the payload is sent once for each person who should be told. Your system sends the WhatsApp message. Nothing comes back to us
except the HTTP answer: there are no delivered or read receipts.

`GET/POST /admin/v1/webhooks` · `PUT/DELETE /admin/v1/webhooks/{id}` · `POST /admin/v1/webhooks/{id}/send-now`
`POST /admin/v1/webhooks/standard` · `GET /admin/v1/webhooks/deliveries` · `POST /admin/v1/webhooks/deliveries/{id}/resend`
`GET/PUT /admin/v1/webhooks/settings` · `POST /admin/v1/webhooks/settings/secret`

* **Create.** `POST /admin/v1/webhooks/standard {url, headers}` makes an endpoint for every built alert that has none, all with the same address
  and headers and each with its standard payload. It skips alerts that already have one, so it can be run again when a new alert is built.
  One-off changes are made on the endpoint (`PUT`). An endpoint can be turned on or off (`{"active": false}`) without losing its settings.
* **Event names.** `system.<alert>` or `smart.<alert>`, for example `system.camera_offline`, `smart.blocked_plate`. Sent as `X-Event`.
* **Shortcodes.** Every payload can use `{customer} {camera} {time} {details} {title} {to_name} {to_phone} {event_type} {alert_id}`
  (`to_phone` is digits only, for example `919876543210`). Each alert adds its own: `{plate} {direction}` for a blocked vehicle,
  `{people} {limit}` for crowding, `{recorder}` for a recorder, and so on. Alerts that carry evidence also have `{image_url}`, and `{clip_url}`
  where there can be a video. A shortcode with nothing to show is an empty string. A shortcode the alert does not have is refused on save.
  Values are escaped, so a quote or a line break in a camera name can never change the structure of the JSON.
* **Standard payload.**
  ```json
  { "phone_number": "{to_phone}", "template_name": "smart_blocked_plate_v1", "customer": "{customer}", "camera": "{camera}",
    "time": "{time}", "details": "{details}", "image_url": "{image_url}", "clip_url": "{clip_url}" }
  ```
* **Headers we add.** `X-Event`, `X-Delivery-Id` (the same on every try, so you can drop a duplicate), `X-Timestamp`, and `X-Signature`
  (`sha256=` + HMAC-SHA256 of `"<timestamp>.<body>"` with the signing secret). `X-Test: true` marks a test. Your own headers (for example
  `Authorization`) are stored encrypted and are never written to the audit log.
* **Picture and clip links.** Public `https://<site>/e/<token>/image.jpg` and `/clip.mp4`, made fresh for each try. They work for the number of
  hours set on the webhook settings (1 to 4, **never more than 4**). A blocked-vehicle event whose payload has `{clip_url}` waits up to
  `GI_CLIP_WAIT_SECONDS` for its video and is released the moment the clip arrives; if it never does, the event goes out with an empty `clip_url`.
* **Retries.** A temporary failure (timeout, 5xx, 408, 425, 429) is tried again after 30 s, 2 min, 10 min, 30 min and 1 h, six tries in all
  (about an hour and three quarters), then the event is marked *failed*. Another 4xx fails at once. A failed event can be sent again from the
  delivery log; it keeps its delivery id.
* **No webhook, no send.** An alert with no active endpoint is still recorded and shown to the customer; its messages are marked *suppressed*
  with "No webhook is set up for this alert yet".
* **Send now** sends one test event with sample values (or a number you give) and shows the answer. Nothing is recorded for any customer.
* **Safe to expose.** https only, no `user:password@`, ports 443/8443, no forbidden headers. On every send the host name is resolved, **every**
  address must be public (loopback, private, link-local and cloud-metadata addresses are refused), and the connection goes to that exact
  address, with TLS verified against the real host name. Redirects are not followed, the answer is cut at 4 KB, and timeouts apply.

## Video clips

A blocked-vehicle alert can carry a short video (5 seconds before the plate was read, 4 after).

```
worker keeps the last ~25 s of analysed pictures (small JPEGs), only when clips are on for the camera
worker reports the plate  ->  backend raises the alert, answers "send a clip for event #n", and holds the webhook event (up to 40 s)
worker waits for the seconds after the moment, encodes H.264 MP4 with FFmpeg, uploads  POST /internal/v1/alerts/{id}/clip
backend attaches it and releases the held event at once  ->  sent with clip_url filled in
```

* **Switches.** The customer: *Include a video clip* under Blocked vehicle (`params.clip`, on by default). The owner: per customer
  (`clips`), because clips cost storage. The worker is told through the camera config (`clips: true`).
* **Never blocks the alert.** If no clip arrives within the wait the event goes out without it. If the endpoint's payload has no `{clip_url}`,
  nothing is held, and the clip still attaches and plays on the evidence page.
* **Late clips still show.** The page behind the link reads the alert when it is opened, so a clip that arrives after the event is there the
  next time it is opened.
* **Format.** H.264 main profile, yuv420p, no audio, `moov` first, at most 960 wide, 5 frames a second, under 15 MB.
* **Retention.** Clips expire with the customer's pictures (`retention_snapshots_days`).

## Evidence on alerts

| Alert | Evidence kept |
|---|---|
| Blocked vehicle seen | picture and, if clips are on, a short video |
| Plate needs checking | the picture of the read |
| Vehicle has stayed too long | the picture taken as the vehicle came in |
| Possible phone use | a picture with the area, the people and the phone marked |
| Too many people in an area, Queue too long | a picture with the area and people marked: the worker takes one when the limit is first reached and another every `picture_minutes` while it stays (the customer sets it on the area, default 15, `0` for no pictures), and the alert shows the newest |
| Too many people (the count) | a picture of the camera that has counted the most people today, taken when the alert is sent. While the crowd stays over the limit a new one is taken only every `picture_minutes` (the customer sets it, default 15, `0` for none), and the alert shows the newest. If the camera cannot be reached the alert still goes out |
| Camera and recorder alerts, account alerts | none: an offline camera has no picture |

* **Nothing identifies a person.** The pictures mark places and people-shaped boxes, never names.
* **How long.** Pictures and clips are deleted after the customer's `retention_snapshots_days`, which only Jas iVision staff change (per customer, 7 days by default). There is no "keep" for customers.
* **In the app.** An alert with a picture shows it when opened (`GET /v1/alerts/{id}/image`, signed-in people of that customer only). The video opens through a link made for that moment
  (`POST /v1/alerts/{id}/evidence-link`, 60 minutes, logged). WhatsApp links are separate and last as long as the webhook setting says (never more than 4 hours).

## Behaviour you can rely on (all covered by tests)

* An alert and its events are written together or not at all; a disabled alert raises nothing; the same alert inside its cool-down (blocked
  plate 10 min, offline 60 min, ...) is raised once.
* An event is claimed with a 3-minute lock; a worker that dies lets the lock lapse and the event is picked up again. Two delivery workers
  never take the same event.
* Quiet hours, the customer's monthly WhatsApp cap, the per-customer switches and a 20-an-hour-per-person limit record the alert and mark the
  event *suppressed* with the reason, visible to the customer.
* Deleting a person cancels their waiting events. Customers cannot see, use or attach each other's people, alerts or pictures. A customer with
  a view-only staff session cannot change alert settings.
* Staff can only see and change webhooks as the owner; every change is in the audit log (header values are not).

## Limits you should know

* **At-least-once.** If the process dies after your system accepted an event but before we record it, the event is sent again once the lock
  lapses. `X-Delivery-Id` lets you drop the duplicate.
* **An evidence link is a bearer link:** anyone who receives it can open it until it expires, and WhatsApp messages can be forwarded. That is
  why the longest life is 4 hours.
* **No receipts.** We know your system answered 2xx, not that WhatsApp delivered the message.
* **Crowding** is the day's people-in minus people-out; a person missed at a door is never corrected, so it drifts. Treat it as a warning,
  not a head count.
* **Alerts that are not built.** Storage, disk fault, recording stopped, clock, camera view and picture quality alerts need a new reading from
  the Dahua recorder or a picture comparison. They are in the catalogue, but cannot be switched on until they are built.


## Reports on WhatsApp (replaces report emails)
A scheduled report is announced as one webhook event per person, `system.report_ready`, with the endpoint named "Report ready" in the staff console (Webhooks, "Add the rest" creates it).
Shortcodes: `{customer}`, `{report}` (for example "Footfall summary"), `{period}` ("3 Nov to 9 Nov"), `{document_url}`, `{to_name}`, `{to_phone}`.
`{document_url}` opens the Excel file without signing in, for the number of hours set in the webhook settings (4 at most); after that the person opens Reports in the app.
Only people with a mobile number can be chosen for a report. Report emails are no longer sent; the SMTP provider is not used by reports.


## What a recorder tells us about itself

The Dahua gateway asks each recorder every 10 minutes and sends the answers to `/internal/v1/recorders/{id}/health`: `disks` (size, free space, state, life left), `clock`,
`recording` (per channel), `days_held` (about how many days of video it keeps, found in steps of 1, 3, 7, 14, 21, 30, 45, 60, 90 days every six hours), `uptime_seconds`,
`channel_online` (does the recorder see each camera) and `hardware` (temperature, fan, camera power ports). The recorder's own alarms (disk error, storage low, a camera covered
or lost, a restart) go to `/internal/v1/recorders/{id}/alarms` and become alerts at once. A reading the recorder cannot give is left out, and nothing is raised for it.
The newest answers are shown on the customer's recorder page and in Platform Admin. When the recorder says it cannot see a camera, the "Camera offline" message says so.

**Survey mode.** Setting `GI_DH_SURVEY=true` for the Dahua gateway makes it ask each recorder, once, a set of read-only questions and write the raw answers to the log
(`journalctl -u gi-dahua | grep -E 'survey|alarm from'`): the state questions, the recorder's own event log for the last three days, and each kind of alarm the recorder announces (written the first time it arrives, with its number). It changes nothing on the recorder. It is for finding out which readings a given recorder model really gives.

## Daily and weekly summaries (WhatsApp Direct Send)

The daily and weekly summary are written by the server for each customer and sent straight to WhatsApp (`messaging/direct.py`). They have no webhook and no template: the provider's
Direct Send address takes the whole text, with its line breaks, and Meta matches or creates the template itself. Every other alert still goes by webhook.

**What is in it** (`services/summaries.py`; the wording of every line is a setting):
- BUSINESS: visitors, busiest hour, most inside at once, vehicles, longest queue (weekly: visitors, busiest day and hour, vehicles). A line appears only when the customer has the feature,
  set up and switched on (people counting; a number-plate camera and a vehicle alert; a queue area and the Long queue alert). With none, the block is left out.
- SYSTEM: cameras and recorders, days of video kept, disks, clocks, alerts in 24 hours, opening hours. Everyone gets it. A line with no reading behind it is left out.
- NEEDS A LOOK: problems from the alerts the customer has on (offline, disks, recording, clock, restarts, heat, picture), worst and newest first, at most the set number and then "+N more".
  When there are none the message says everything is fine.

**When:** at the customer's hour (their alert setting, default 08:00). The weekly summary goes on the weekly day (Monday) instead of the daily one; a customer with the weekly summary off still
gets the daily one. Both are on for everyone unless Platform Admin or the customer switches them off. **Who:** the customer's own recipients; with none chosen, the account owner (the first admin, if
their number is on the People list), all admins, or nobody, as Platform Admin says.

**Sending:** one `POST` per person: `{"source", "to", "content", "category": "utility"}` with `Authorization: Bearer <key>`. Any 2xx counts as accepted (the provider answers 202 and delivers a little
later; its `message_id` is kept). A 5xx, 408/425/429 or a connection failure is tried again up to the set number of times; any other 4xx is final. It never retries after an accepted answer. The gap
setting spaces out customers who share a send hour. The queue, locks and "Send again" are the same as for webhooks (`messaging/delivery.py`).

**Platform Admin → WhatsApp summaries** holds the address, the key (encrypted, never shown again), source label, category, wait time, retries, gap, longest message, the schedule, the recipients rule
and the wording of every line, with a live preview from a real customer, a test send to any number, and the recent sends. The key is checked only by a test message: the provider has no way to
test a key without sending one.

**Phone:** the alert's title is "Daily system summary" or "Weekly system summary"; the text is a short headline and its Tamil line. **App:** the Home screen shows the newest summary whole (`GET /v1/summary`).

**Check one customer without sending:** `python -m scripts.print_summary --customer "Jas Office" [--kind weekly]`.


## What the AI sees, drawn on every clip

Each picture that goes into an alert's video has what the AI found drawn on a copy (`worker/overlay.py`): a green box round each person (a yellow one round each vehicle that is followed), each marked area with its name and the number of people in it (the outline turns orange while the area is at its limit), the counting line with its IN arrow, the plate zone, and "People in view: N". There are no names, track numbers or percentages. It applies to every customer and every clip made from analysed pictures (plate and marked-area alerts); the picture the analysis works on is never changed, and if drawing fails the plain picture is used.

The pictures are saved after the analysis of each picture (`CameraPipeline.process`), so the boxes match that moment. The clip still plays at 5 pictures a second and is made from the pictures that were analysed (about 2 a second while nothing is in view and about 5 while something is); a smoother clip is the next step.
