# Alerts: System Alerts, Smart Alerts and webhooks

## The two families

| Family | What it is | Shown with |
|---|---|---|
| **System Alerts** | Keep the cameras, recorders and account healthy: a recorder or camera goes offline, storage is nearly full, and so on | no icon |
| **Smart Alerts** | Come from the AI on the camera pictures or from rules about the customer's business: a blocked vehicle, too many people, a vehicle that stayed too long | the AI sparkle icon |

The catalogue lives in `app/services/alert_catalog.py`: 20 alerts, each with its family, group, title, severity, settings, shortcodes and
whether it is **built**. Thirteen are built today; the rest are listed so staff can see what is coming, but nothing can raise them and no
customer sees them.

| Alert | Family | What makes it fire | Settings the customer can change |
|---|---|---|---|
| Recorder offline | System | No heartbeat for `GI_HEARTBEAT_TIMEOUT_SECONDS` | none |
| Camera offline | System | The worker stopped reporting for a minute | none |
| Connection keeps dropping | System | The recorder went offline `drops` times in the last hour (connection history) | `drops` (5; 2 to 20). Once every 6 hours at most |
| Back to normal | System | A recorder or camera is working again **and the customer was told it was down in the last 24 hours** | none |
| Plan limit nearly reached | System | Enabled cameras reach `percent` of the plan's limit | `percent` (90; 50 to 100). Once a month at most |
| Message allowance nearly used | System | This month's WhatsApp messages reach `percent` of the customer's cap (only for a customer with a cap) | `percent` (80; 50 to 100). Once a month at most |
| Payment due or overdue | System | An issued invoice is due within `days`, and again when it is overdue. Paid and void invoices never | `days` (3; 0 to 14) |
| New sign-in on your account | System | A person signs in from a browser and system they have not used before (the first one ever is not reported) | none |
| Daily health summary | System | Once a day at `hour`, in the customer's time zone: cameras working, recorders online, alerts in 24 hours | `hour` (8; 0 to 23). **Off until the customer turns it on** |
| Blocked vehicle, Vehicle stayed too long, Plate needs checking | Smart | Number plates | see the Smart Alerts section |
| Too many people | Smart | People counting | `people` |

## Smart Alerts on marked areas

Three Smart Alerts work on **areas** the customer marks on a camera picture (camera page, "Marked areas"). Each area has a name and **its own limits**, so two
counters can have two limits. The alert (on or off, who is told, quiet hours) is set on the Alerts page as for any other alert. **An event is sent only when the
condition is met**, and the same area is not reported again for `repeat_minutes` (default 15).

| Alert | Area kind | Condition (the customer sets the numbers) | Shortcodes |
|---|---|---|---|
| Too many people in an area | `area_count` | The count stays at `limit` or more for `minutes` (every report in that time) | `{area} {people} {limit} {minutes}` |
| Queue too long | `queue_area` | The queue stays at `length` or more for `minutes`, **or** the longest wait reaches `wait_minutes` (0 turns the wait off) | `{area} {length} {limit} {minutes} {wait_minutes}` |
| Possible phone use in a staff area | `staff_area` | A phone has been in use for `seconds` without a break. Comes with a picture | `{area} {seconds}` and `{image_url}` |

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
* **The platform owner** sets up the webhooks (below), can switch WhatsApp off or set a monthly cap for one customer
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
