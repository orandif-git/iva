# Alerts to WhatsApp and email

## How a message travels

```
event (blocked plate, camera offline, ...)
  -> raise_alert()      one transaction: the alert row + one queued message per person per channel   (nothing can be lost)
  -> delivery service   claims queued messages (FOR UPDATE SKIP LOCKED), sends through the ACTIVE provider, records the result
  -> provider reports   POST /hooks/messaging/<provider id>   sent -> delivered -> read   (never moves backwards)
```

* **Customers** choose, in their portal, which alert types are on, who is messaged (WhatsApp / email), thresholds and quiet hours
  (`GET/PUT /v1/alert-types`, `/v1/recipients`). Entering a phone number requires ticking *"this person agreed to receive WhatsApp
  messages"*; who ticked it and when is stored (`consent_confirmed_by/at`). Changing the number asks again.
* **The platform owner** configures the provider(s) and templates in the admin console (`/admin/v1/messaging/...`), and can switch
  WhatsApp/email off or set a monthly WhatsApp cap per tenant.
* **Pictures** are uploaded by the AI worker to `POST /internal/v1/evidence`, stored in a **private** R2 bucket, and people receive
  `https://<site>/e/<token>` (a page) and `/e/<token>/image.jpg` (the picture itself, which the WhatsApp provider fetches for the
  header). Tokens are signed, carry their expiry (default 72 h) and are made at *send* time, so a retried message gets a fresh one.
  Files are deleted after the tenant's picture period (`retention_snapshots_days`, default 7, set in the admin console).

## The provider recipe (the "Postman" part)

A WhatsApp provider is one JSON recipe plus named secrets. Secrets are encrypted at rest, never returned by the API, never written to
the audit log, and masked in previews and error messages.

```json
{
  "method": "POST",
  "url": "https://bsp.example.com/v1/messages",
  "headers": { "Authorization": "Bearer {{secret.api_key}}" },
  "body": {
    "to": "{{recipient.phone_digits}}",
    "reference": "{{message.id}}",
    "template": {
      "name": "{{template.name}}",
      "language": "{{template.language}}",
      "components": [
        { "$if": "media.is_image", "then": { "type": "header", "parameters": [{ "type": "image", "image": { "link": "{{media.url}}" } }] } },
        { "$if": "media.is_video", "then": { "type": "header", "parameters": [{ "type": "video", "video": { "link": "{{media.url}}" } }] } },
        { "type": "body", "parameters": [{ "$each": "vars", "do": { "type": "text", "text": "{{item}}" } }] },
        { "$if": "link.token", "then": { "type": "button", "sub_type": "url", "index": "0", "parameters": [{ "type": "text", "text": "{{link.token}}" }] } }
      ]
    }
  },
  "success": { "status": [200, 201, 202] },
  "message_id_path": "messages.0.id",
  "error_path": "error.message",
  "cost_paise": 85,
  "webhook": {
    "auth": { "type": "hmac_sha256", "header": "X-Signature", "secret": "webhook_secret", "prefix": "sha256=" },
    "events_path": "events", "id_path": "id", "status_path": "status", "error_path": "reason",
    "status_map": { "sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed" }
  }
}
```

Secrets for this example: `api_key`, `webhook_secret`. Your own provider's field names go in the same places; if it takes a plain
text message or a different layout, only the `body` changes.

| Variable | Meaning |
|---|---|
| `recipient.phone`, `.phone_digits`, `.name`, `.email` | `+919876543210`, `919876543210` |
| `template.name`, `template.language` | the provider-side template name, e.g. `gi_alert_image`, `en` |
| `vars` / `vars.1` … | the template variables in order (`$each` repeats a part once per variable) |
| `media.url` | direct link to the picture or the clip (empty when there is none; `$if` drops the part) |
| `media.type`, `media.is_image`, `media.is_video` | `image` or `video`; the two flags are `1` or empty, for choosing the right header |
| `link.token`, `link.url` | the evidence token (for a URL button's dynamic suffix) / the full page link |
| `message.id` | our message id, a reference you can send so the provider can de-duplicate |
| `alert.title`, `.body`, `.camera`, `.time` | for providers that send free text |
| `secret.<name>` | a stored secret (headers or body) |

**Why this is safe to expose.** The recipe is parsed JSON; values are put into *string values only*, so a camera name or plate can never
add a field, close a quote or start a header (`CR/LF` in a header is refused; variables in URLs are percent-encoded). On save the recipe
is checked: https only, no `user:password@`, ports 443/8443, no unknown variables, no forbidden headers (`Host`, `Content-Length`, ...).
On every send the host name is resolved, **every** address must be public (loopback, private, link-local, cloud-metadata are refused),
and the connection goes to that exact address (so a name that changes its answer afterwards cannot redirect us), with TLS verified
against the real host name. Redirects are not followed, the answer is cut at 64 KB, and timeouts apply.

**Try it before use.** `POST /admin/v1/messaging/providers/{id}/test` with `"send": false` shows the exact request (secrets masked);
with `"send": true` it sends one real message.

## WhatsApp templates to submit (drafts)

All are category **Utility** (a transactional alert the customer asked for), language **English**. Install them as drafts with
`POST /admin/v1/messaging/templates/install-defaults`, then `GET /admin/v1/messaging/templates/{id}/submission` gives the exact fields
to paste into your provider's form. After the provider/Meta approves each one, mark it with `PUT .../templates/{id}/status?status=approved`.
Only approved templates are ever used; if the picture template is not approved yet the system falls back to the link one, then plain text.

**1. `gi_alert_image`** — header: **Image** (sample: any picture) · button: **URL**, text `View evidence`, URL `https://app.example.com/e/{{1}}`, sample `Ab3dEf`
```
Security alert from Jas iVision.

Event: {{1}}
Location: {{2}}
Time: {{3}}
Details: {{4}}

Tap the button below to see the full evidence.
```
Samples: `Blocked vehicle TN72AB1234` · `Main Gate` · `05 Nov, 10:30 AM` · `Seen entering`

**2. `gi_alert_link`** — no header · same button, same body, same samples (used until 1 is approved, and for alerts whose picture is missing).

**3. `gi_alert_text`** — no header, no button (camera/recorder offline, overstay, crowding)
```
Jas iVision notice.

Event: {{1}}
Location: {{2}}
Time: {{3}}
Details: {{4}}

Please check on site or contact support.
```
Samples: `Camera offline` · `Main Gate` · `05 Nov, 10:30 AM` · `No video for 5 minutes`

**4. `gi_alert_clip`** — header: **Video** (sample: a short MP4), otherwise as 1. Used when an alert has a clip.

Rules these follow: no variable at the very start or end of the body, variables numbered in order, a sample for each, no promotional
wording (the console rejects those before you submit), header media type fixed per template. Variable values are flattened to one line
and trimmed (WhatsApp rejects line breaks and long runs of spaces inside a variable).
**Change nothing in an approved template**: a new wording is a new template and a new approval.

## Video clips

A blocked-vehicle alert can carry a short video (5 seconds before the plate was read, 4 after).

```
worker keeps the last ~25 s of analysed pictures (small JPEGs), only when clips are on for the camera
worker reports the plate  ->  backend raises the alert, answers "send a clip for event #n", and holds the WhatsApp message (up to 40 s)
worker waits for the seconds after the moment, encodes H.264 MP4 with FFmpeg, uploads  POST /internal/v1/alerts/{id}/clip
backend attaches it and releases the held message at once  ->  sent with template gi_alert_clip and a video header
```

* **Switches.** The customer: *Include a video clip* under Blocked vehicle (`params.clip`, on by default). The owner: per tenant
  (`clips`), because clips cost storage. The worker is told through the camera config (`clips: true`) and only records for plate cameras
  with clips allowed; a camera that is off costs nothing.
* **Never blocks the alert.** If no clip arrives within the wait (worker down, too few pictures, encoding failed, upload refused),
  the message goes out as the picture message. If `gi_alert_clip` is not approved, nothing is held: the picture message goes at once,
  and the clip still attaches and plays on the evidence page.
* **Late clips still show.** The page behind the button reads the alert when it is opened, so a clip that arrives after the message is
  there the next time the link is opened. The page plays it (`<video>`, still picture as poster); `/e/<token>/clip.mp4` is the file itself
  for the provider, with byte ranges (needed by phones and by some providers) and `HEAD`.
* **Format.** H.264 main profile, yuv420p, no audio, `moov` first, at most 960 wide, 5 frames a second, under 15 MB (WhatsApp's limit is
  16 MB). An oversize clip is re-encoded once smaller, then dropped.
* **Smoothness.** A clip shows what was *analysed*. With the default adaptive rate (2 fps while nothing is in view, 5 while something is),
  the seconds before a vehicle appears are sparser than the seconds after. Raise the active rate for the camera for smoother evidence.
  A reconnect in the middle is never bridged with a frozen picture: only the unbroken stretch around the moment is used.
* **Retention.** Clips expire with the tenant's pictures (`retention_snapshots_days`).
* **Cost to plan for.** A typical 9-second 960×540 clip is a few hundred KB; a busy gate with many blocked-list hits multiplies that by
  the picture-retention days. A clip is stored once and linked, not copied per person.

## Behaviour you can rely on (all covered by tests)

* An alert and its messages are written together or not at all; a disabled alert type raises nothing; the same alert inside its
  cool-down (blocked plate 10 min, offline 60 min, ...) is raised once.
* A message is claimed with a 3-minute lock; a worker that dies lets the lock lapse and the message is picked up again. Two delivery
  workers never take the same message.
* Temporary failures (timeouts, 5xx, 429, 408) retry after 30 s, 2 min, 10 min, 30 min, 2 h, then fail. Permanent failures (other 4xx,
  a refused address, a rejected recipe) fail at once. **A failed WhatsApp message is repeated as an email** when the person has an
  address and email is on.
* With no active provider, or no approved template, messages **wait** (retrying) instead of being dropped; fix it in the console and
  they go out.
* Quiet hours, the tenant's monthly WhatsApp cap, the per-tenant switches and a 20-an-hour-per-person limit record the alert and mark
  the message *suppressed* with the reason, visible to the customer.
* Deleting a person cancels their waiting messages. Tenants cannot see, use or attach each other's people, alerts or pictures.
* Delivery reports are verified (HMAC or shared header) and can only move a message forward.

## Limits you should know

* **At-least-once.** If the process dies after the provider accepted a message but before we record it, the message is sent again once
  the lock lapses. The `reference` field (our message id) lets a provider that supports it drop the duplicate.
* **An evidence link is a bearer link:** anyone who receives it can open it until it expires, and WhatsApp messages can be forwarded.
  Keep the expiry short; the owner can lower `GI_EVIDENCE_LINK_TTL_HOURS`.
* **SMTP host check** resolves the name and refuses private addresses, but the SMTP library then connects by name, so a name that
  changes its answer between the check and the connection is not pinned (owner-only setting; HTTP providers are fully pinned).
* **Crowding** is the day's people-in minus people-out; a person missed at a door is never corrected, so it drifts. Treat it as a
  warning, not a head count.
* **Clips were encoded and played back only from synthetic pictures**, not from a real camera, and the video header has not been sent to a real
  WhatsApp provider. Real footage compresses differently (a busy scene can reach the size limit sooner); the worker measures and retries smaller.
* **Nothing here has been run against a real WhatsApp provider, Meta, or Cloudflare R2.** Tests use a local HTTPS mock provider (real
  TLS, real address pinning), a fake S3 and a local mail server. R2 specifics (path-style addressing, no extra checksums) follow its
  documentation but are untested against R2 itself. Template approval is manual and can be rejected by Meta.
* **Not built:** push notifications, per-recipient language, an admin screen (only the API exists), and a customer-facing screen (mock-ups not updated).
