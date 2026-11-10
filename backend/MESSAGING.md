# Alerts: System Alerts, Smart Alerts and webhooks

## The two families

| Family | What it is | Shown with |
|---|---|---|
| **System Alerts** | Keep the cameras, recorders and account healthy: a recorder or camera goes offline, storage is nearly full, and so on | no icon |
| **Smart Alerts** | Come from the AI on the camera pictures or from rules about the customer's business: a blocked vehicle, too many people, a vehicle that stayed too long | the AI sparkle icon |

The catalogue lives in `app/services/alert_catalog.py`: 20 alerts, each with its family, group, title, severity, settings, shortcodes and
whether it is **built**. Six are built today (recorder offline, camera offline, blocked vehicle, vehicle overstay, too many people, plate
needs checking). The rest are listed so staff can see what is coming; nothing can raise them and no customer sees them.

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
