# Jas iVision backend (FastAPI)

First slice: platform admin (tenants, recorders, view-as-customer, audit), customer onboarding (recorders, cameras, zones),
and the internal routes the recorder registrar calls. See `../API.md` for the full route plan and `../schema.sql` for the database.

## Web pages and sign-in

`web/` holds the Control Center (`/console`) and its sign-in and set-password pages (`/`, `/set-password`). They are plain HTML, CSS and JavaScript served by the API itself
(no build step, no inline scripts or styles) and sent with a strict content policy.
Sign-in is in `app/services/auth.py`: email and password only (scrypt); refresh tokens work once; lockout and rate limits. There is no customer sign-in.
Create the first staff account with `python -m scripts.create_staff`. Deployment steps: `DEPLOY.md` sections 6 and 6c.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 1. Database: create it, apply ../schema.sql as a superuser, then create the login role the API uses.
createdb gateinsight
psql gateinsight -f ../schema.sql
psql gateinsight -c "CREATE ROLE gi_api LOGIN PASSWORD 'change-me'; GRANT app_user, platform_admin, service_worker TO gi_api; GRANT CONNECT ON DATABASE gateinsight TO gi_api;"

# 2. Configuration
cp .env.example .env        # fill in the secrets as the comments describe

# 3. Start
uvicorn app.main:app --reload
# Interactive docs at http://localhost:8000/docs (development only)

# 4. Demo data and tokens
GI_SEED_DSN=postgresql://postgres@localhost/gateinsight python -m scripts.seed_dev
```

## Test it

The tests use a real PostgreSQL, because row-level security and the audit trigger are what they check. Start any
throwaway server and point the tests at it:

```bash
TEST_PG_HOST=/tmp/pgtest TEST_PG_PORT=55432 python -m pytest -q
```

## How the safety properties are enforced

| Property | Where |
|---|---|
| A customer can only see their own tenant | Row-level security in the database. The API sets the tenant from the verified token, then switches to the restricted `app_user` role for the request. |
| Staff, customer and service tokens never interchange | Separate JWT audiences and secrets (`app/security.py`, `app/deps.py`) |
| Roles can be revoked instantly | Staff and customer roles are read from the database on every request, not trusted from the token |
| View-as-customer cannot change anything | Non-GET requests are refused, and the transaction is `READ ONLY`. Sessions expire and end when a tenant is suspended. |
| The audit log cannot be altered | Database trigger and revoked privileges. The tests try to update and delete it. |
| Recorder keys are protected | AES-GCM at rest, bound to the device id, shown once, never in logs or the audit log (`app/crypto.py`) |

## Before production

- Replace `issue_token` and the local key vault with your identity provider (RS256, JWKS) and a KMS.
- Put `/internal/*` on a private listener with mTLS, and add rate limiting at the gateway.
- Run behind a proxy with `--proxy-headers` so audit rows record the real client IP.
- Add database migrations (Alembic or similar). `../schema.sql` is the starting point.
- Not built yet: footfall and plate event ingestion and queries, alerts, reports, billing, partner API, the registrar itself.

---

# Registrar (GB28181)

`registrar/` is a separate process. Recorders connect **out** to it over SIP (UDP or TCP, port 5060), so customers need no static IP,
VPN or router changes. It talks to the backend only through `/internal/v1` over HTTP.

```
Recorder --SIP REGISTER (Digest, password = key)--> registrar --GET credentials--> backend
Recorder <--catalogue query-- registrar        Recorder --channel list--> registrar --heartbeat--> backend
Recorder <--live-video request-- registrar     Recorder --RTP packets--> registrar --preview-ok--> backend
Recorder --keepalive every ~60 s--> registrar   (silence for 3 minutes = offline)
```

## Run it

```bash
GI_REG_BACKEND_URL=http://127.0.0.1:8000 \
GI_REG_SERVICE_TOKEN=<same value as the backend GI_SERVICE_TOKEN> \
GI_REG_PUBLIC_IP=<address recorders can reach> \
python -m registrar
```

Other settings (`GI_REG_` prefix): `SIP_PORT` (5060), `REALM`, `SERVER_ID` (these two **must equal** the backend's `GI_GB_REALM` and
`GI_GB_SERVER_ID`), `RTP_PORT_MIN`/`RTP_PORT_MAX` (open this UDP range in the firewall), `KEEPALIVE_TIMEOUT`, `HEARTBEAT_INTERVAL`.

## What the customer enters on the recorder (GB28181 page)

| Recorder field | Value |
|---|---|
| SIP server IP / address | `server` from the registration response |
| SIP server port | `port` (5060) |
| SIP server ID | `server_id` |
| SIP domain / realm | `domain` |
| Device ID | `device_id` (20 digits) |
| Password | `key` |
| Register validity / heartbeat | 3600 s / 60 s |
| Transport | UDP (or TCP if the recorder offers it) |

## Security properties

- Digest authentication with single-use nonces bound to the requesting address; replayed logins are refused.
- Unknown device, wrong key, wrong protocol and suspended tenant all get the same `403`, so device ids cannot be probed.
- 10 failed logins in a minute from one address are ignored for the rest of the window.
- Messages are accepted only from a registered device, from the address it registered from.
- XML with DTDs or entities is refused; message sizes, item counts and the nonce table are all capped.
- Keys and digests are never logged.
- If the backend is down the recorder gets `503` (it retries) rather than `403` (it would give up).
- `MD5` is used for Digest because the standard requires it. Plain SIP and RTP are not encrypted: do not carry them over the open internet
  if that is a concern for a customer; use a VPN for those sites.

## Not covered

- **Hikvision is not supported** (product decision, November 2026): Hikvision recorders stay on Hik-Connect. A trial with Hikvision's Device Gateway (ISUP 5.0) registered a recorder and read its status, but live video was never proven, so the work was parked. Creating a Hikvision or ISUP recorder is refused.
- **Dahua Auto Register** is the product's route (`python -m dahua`, needs Dahua's NetSDK Python wrapper; see DEPLOY.md 10b). GB28181 is also implemented as a fallback, but only staff can add GB28181 recorders and the "other" brand (`POST /admin/v1/tenants/{id}/recorders`).
- **Real recorders.** All tests use a simulated recorder written from the standard. Real units have quirks (field names, timing,
  which transport they prefer, unusual catalogue contents). Test every model before promising support for it.
- **Live video for analysis.** The registrar proves video flows (counts RTP packets, then hangs up). Relaying streams to the AI
  workers is a separate component (a media server such as ZLMediaKit is the usual choice).
- **Scaling.** One process holds all sessions in memory. For several instances, put recorders on stable per-instance addresses or add shared session state.

---

# Footfall and plate events

**Workers write** (`/internal/v1/events/*`, service token): batches of footfall minutes and plate reads. The tenant is taken from the
camera, never from the request. A bad event is reported by index and the rest of the batch is still stored.

**Customers read** (`/v1/footfall`, `/v1/footfall/summary`, `/v1/plates`, `/v1/plates/review-queue`, `/v1/plates/{id}`, `/v1/vehicle-lists`).

How reads are handled:

| Step | Rule |
|---|---|
| Cleaning | Upper-case, strip spaces and dashes. Each position of an Indian plate is known to be a letter or a digit, so one look-alike mistake (O/0, I/1, S/5, B/8, G/6, Z/2) is fixed. Series letters never become I or O, because the series skips them. |
| Trust | A read goes to the review queue when it is not a valid plate, needed 2 or more fixes, or has confidence under 70. The raw OCR text is always kept in `plate_raw`. |
| Duplicates | Same camera and track id, or same plate, camera and direction within 2 minutes, counts once. Retrying a batch is safe. |
| Entry and exit | An exit is paired with the latest unpaired entry of the same plate in the last 7 days, giving the time inside. Doubtful reads are never paired automatically. |
| Blocked plates | A confident read of a blocked plate creates one critical alert (at most one per plate per 10 minutes). Delivering that alert (WhatsApp, email) is not built yet. |
| Footfall retries | A worker sends the running total for a minute. The larger figure wins, so duplicates and stale resends never double count. |
| Corrections | A person must type a complete valid plate. Nothing is guessed. Every correction is audited. |
| Storage | Plate events are partitioned by month. The backend creates the previous, current and next months automatically (`services/maintenance.py`). |

Not built: live footfall stream, evidence image URLs (needs object storage), alert delivery, retention clean-up, reports.

---

# AI worker (`worker/`)

Reads a video, finds people and vehicles, counts people crossing a line, reads plates of vehicles in a zone, and posts the results to
the backend's `/internal/v1/events/*` routes. It starts from a video file; the same pipeline will later be fed live streams.

```bash
pip install -r requirements-dev.txt -r requirements-worker.txt
python -m scripts.fetch_models                       # YOLOX detector (checksum-verified) and the plate models

# Try it on a file, without a backend:
python -m worker run-file --video gate.mp4 --camera-id <any-uuid> --zones-json zones.json --dry-run --snapshot-dir ./snaps

# Or against the backend (it supplies the zones and settings; the camera must exist and have footfall and/or plates switched on):
GI_WORKER_BACKEND_URL=http://127.0.0.1:8000 GI_WORKER_SERVICE_TOKEN=<GI_SERVICE_TOKEN> \
python -m worker run-file --video gate.mp4 --camera-id <camera uuid>
```

`zones.json`: `{"name": "Gate", "use_footfall": true, "use_anpr": true, "zones": [{"kind": "count_line", "points": [[0.1,0.5],[0.9,0.5]], "in_direction": [0,1]}, {"kind": "anpr_zone", "points": [[0,0.3],[1,0.3],[1,1],[0,1]], "in_direction": [0,1]}]}`
(coordinates are fractions of the picture width and height; `in_direction` is the way of travel that counts as "in").

## How it works

| Stage | What it does |
|---|---|
| Source | Samples the video to 5 frames per second and stamps each frame with its time (the clip ends "now" unless `--start-time` is given). |
| Detector | YOLOX (Apache-2.0) on ONNX Runtime, CPU. Finds people, cars, motorcycles, buses and trucks. About 50 ms per frame on 2 threads. |
| Tracker | ByteTrack-style: keeps each person or vehicle's identity between frames, bridges short disappearances, and ignores weak one-off detections. |
| People counting | A person counts when their track crosses the line through its length (not around the end). A small dead band ignores jitter on the line. A person who turns back counts out. |
| Plate reading | For vehicles inside the plate zone: find the plate, pad the crop by 10%, read it, up to 6 reads per vehicle. The reads are combined by confidence-weighted voting for each character position. One event is sent when the vehicle leaves. |
| Delivery | Batches with retries and growing delays, a bounded buffer that counts anything it drops, and an immediate stop if the backend says this camera may not send data. Footfall is sent as the minute's running total, so retries and re-runs never double count. |

`--anchor foot` counts the bottom of a person's box instead of the centre, which suits angled cameras; the default centre suits overhead ones.

## Models and licences

- **YOLOX** (Apache-2.0) is used instead of YOLOv8 because the YOLOv8 family is AGPL-3.0, which is a poor fit for a closed commercial service.
- **Plate detector** (`open-image-models`) and **OCR** (`fast-plate-ocr`) are MIT-licensed packages. Check the licence of the trained weights and
  their training data with your lawyer before selling a service that depends on them.

## What was and was not verified

Verified: tracking, counting, voting, delivery, retries and the whole path into the backend and database, using synthetic videos; the
YOLOX decoding on real footage (people and trucks boxed correctly); the real plate models on rendered plates (about 90% exact on single
reads of blurred, tilted renderings, higher with voting); and the command line on a real 60 s video at about 13 frames/s on 4 CPUs.

**Not verified: accuracy on real Indian plates, real entrances, night, rain or motion blur.** There is no real CCTV footage here to measure
with. Rendered plates in one font are far easier than real ones. Before promising a number to a customer, run a week of their footage
and compare against a manual count. The review queue is the safety net: anything invalid, doubtful or low-confidence goes to a person.

## Not built yet

- Live inputs (the registrar's video relay, RTSP). Only video files for now.
- Running many cameras in one process; each run handles one camera and one file.
- Night-time handling, camera-shake compensation, and a "no vehicle" idle mode that skips detection.
- Evidence pictures are saved to a local folder; uploading them to object storage is not built.

---

# Live input and adaptive frame rate

```bash
# The stream address comes from the backend (set it in the admin console), or from GI_WORKER_RTSP_URL:
GI_WORKER_BACKEND_URL=http://127.0.0.1:8000 GI_WORKER_SERVICE_TOKEN=<GI_SERVICE_TOKEN> python -m worker run-live --camera-id <uuid>
```

Runs until stopped (SIGTERM or Ctrl-C), then closes open vehicle visits, sends what is pending and reports the camera offline.
`--rtsp-url` also accepts a video file, played at real speed, which is how to try the live path without a camera. (Putting a password on the
command line shows it to other users of the machine, so prefer the admin console or the environment variable.)

## Frame-rate policy

| Setting | Meaning | Default |
|---|---|---|
| mode `adaptive` | Analyse at the **idle** rate while the scene is empty, at the **active** rate while a person or vehicle is in view, and drop back after a 3 s hold | yes |
| idle rate | 1-10 fps | 2 |
| active rate | 1-25 fps | 5 |
| mode `fixed` | Always analyse at the **fixed** rate | |
| fixed rate | 1-25 fps | 5 |

Set per tenant in the admin console (owner, finance); any part can be overridden per camera (owner, technician). A camera's own value wins and
empty parts follow the tenant. Compute cost grows in direct proportion to the rate, which is why customers cannot change it.

How adaptive works: FFmpeg delivers pictures at the **active** rate; the controller analyses only some of them while idle. A cheap
frame-difference check wakes it between idle samples, so the first look at a new arrival comes straight away. A vehicle that stops in view keeps the rate up.
On a still scene the motion check stays quiet; on constant movement (swaying trees) it is capped at one extra look per half second.

## Behaviour on a real stream

- **Reconnects forever** with growing delays (1, 2, 4, 8, 16, 30 s, with jitter). A connection that is open but silent for 8 s is restarted.
- **Always the present:** a short queue drops the oldest picture when analysis falls behind, and the count is reported (`frames_dropped`).
- **Time:** each picture is stamped when it arrives. After a reconnect, tracking restarts (nobody is followed across the gap) and open vehicle visits are closed.
- **Passwords** are removed from logs, error reports and the audit log. The address is stored encrypted, bound to the camera.
- **Health** goes to the backend every 15 s even when nothing is happening; a camera that stops reporting is marked offline after 60 s.
- **Settings** are re-read every 30 s: a frame-rate change, new zones or a new stream address all apply without a restart.
- FFmpeg's RTSP timeout option is `-timeout` from version 5 on (older versions: `-stimeout`). The worker asks the installed FFmpeg which to use.

## Limits you should know

- **One worker cannot keep up with a high rate.** Measured: YOLOX-tiny takes about 35 ms per 640x360 picture on 2 threads and about 54 ms at 960x540, so one camera
  at 15 fps on this machine analysed about 8 fps and dropped the rest. The admin console shows "falling behind" when this happens. Use more CPU, the
  nano model (about 3x faster, accuracy not compared), or a lower rate.
- **Tracking needs overlap.** A person moving more than their own width between analysed pictures cannot be linked, which is what a higher active rate is for.
- **Adaptive starts slowly on a first sighting** (up to one idle interval, or at once if the motion check fires). Counting needs the person seen on one side of the line first, so
  set the idle rate with the width of your view in mind.
- **Not verified against a real RTSP camera or server.** The reader is tested with a stand-in FFmpeg (frame cutting, reconnects, stalls, masking), with real FFmpeg on files
  (exact pictures, requested rate, rate clamped to what the camera makes) and against a refused RTSP connection (options accepted, error handled and masked). To try a real
  session: `GI_TEST_RTSP_URL=rtsp://... python -m pytest tests/test_worker_live.py -k real_rtsp`. Start-up time on a real camera is not measured.
- One process runs one camera. Several cameras per process, and a limit on workers per machine, are not built.

---

# Video relay (from a registered recorder to the AI worker)

Recorders that registered over GB28181 send video as an MPEG program stream inside RTP packets. The relay, which runs inside the registrar process,
asks a recorder for a stream when a worker wants one, puts the packets back in order, removes the RTP wrapping and serves the plain stream over HTTP.
FFmpeg reads that directly (`-f mpeg -i http://...`), so **no media server is needed**.

```
recorder --RTP (UDP or TCP)--> registrar/relay --HTTP program stream--> FFmpeg (in the worker) --pictures--> analysis
                                   ^
worker --POST /relay/v1/streams (service token)--> relay: "device X, channel N" -> a single-use play link (valid ~1 minute)
```

The backend tells a worker which route to use: `GET /internal/v1/cameras/{id}/source` answers with an RTSP address (set by an administrator, wins) or with
`{ kind: "gb28181", device_id, channel_no }`. The worker needs `GI_WORKER_RELAY_URL` (default `http://127.0.0.1:8081`) and the same service token.

## Settings (registrar, `GI_REG_` prefix)

| Setting | Default | Meaning |
|---|---|---|
| `RELAY_HTTP_BIND` / `RELAY_HTTP_PORT` | `127.0.0.1` / `8081` | Where workers reach the relay. Keep it on a private address. `-1` turns the relay off, `0` picks a free port. |
| `RELAY_PUBLIC_BASE` | built from the above | The address written into play links, if workers reach the relay by another name |
| `RELAY_MEDIA_TRANSPORT` | `udp` | `udp` works with every recorder. `tcp` loses nothing and passes firewalls better (the recorder connects to us), so prefer it across the internet if the recorder supports it |
| `RTP_PORT_MIN` / `RTP_PORT_MAX` | 30000 / 30999 | Open this range (UDP, or TCP in tcp mode) from the recorders to the registrar |
| `RELAY_IDLE_GRACE` | 10 s | A stream stays open this long after the last viewer leaves, so a reconnecting worker does not make the recorder restart it |
| `RELAY_STALL_SECONDS` | 10 s | No video for this long ends the stream |
| `RELAY_MAX_STREAMS` / `_PER_DEVICE` | 100 / 4 | Recorders only serve a few live streams at once |

## What it guarantees (all tested)

- Every byte the recorder sent arrives in order, over UDP and over TCP. Packets that arrive swapped are put back in order; lost ones are skipped and counted, not waited for.
- Many viewers share one recorder stream (one INVITE). The recorder is told to stop after the last viewer leaves, and when it goes offline all its streams end.
- A viewer that cannot keep up is disconnected instead of being fed damaged video; the worker reconnects.
- Only the recorder's own address may send media. Other addresses, other stream ids and garbage are ignored and counted.
- Play links are single-use and expire in a minute, so the long-lived service token never appears in a command line or process list.
- Limits per recorder and overall; a recorder that refuses or goes silent gives the viewer a clear error and leaks nothing.

## Measured, and what is not covered

- **Real video was decoded end to end:** H.264 in a program stream went recorder -> relay -> FFmpeg -> pictures, and ran as separate processes
  (backend, registrar and relay, worker with the real detector) with 0 packets lost on a local network.
- **Joining takes a few seconds.** A new viewer gets video from "now", and FFmpeg needs about 1.5 s to analyse the stream plus the wait for the next
  keyframe: about 4 s measured (median 4.2 s, worst 5.2 s). Smaller probe settings did not help and made 2 of 6 starts fail. Every reconnect or frame-rate change pays this.
  Fixing it means the relay keeping the latest keyframe group and handing it to a new viewer at once; that depends on how real recorders structure their streams, so it is not built.
- **Not tried against a real recorder.** The stand-in recorder was written from the standard. Real units differ: some ignore the requested stream id, some only do UDP,
  some send H.265 (FFmpeg should decode it, but only H.264 was tested), and some use RTP padding or header extensions (handled and tested at the packet level). Test each model.
- The worker cannot ask a relay stream for its frame rate, so FFmpeg's `fps` filter is asked for the requested rate; if the recorder sends fewer frames the filter repeats some.
- The relay speaks plain HTTP and the recorder's video is unencrypted: run it on a private network or behind a VPN.
- One registrar process holds all sessions and streams. Running several, and sharing streams between them, is not built.


## Alerts: System Alerts, Smart Alerts and webhooks

See [MESSAGING.md](MESSAGING.md): System Alerts and Smart Alerts, plans and per-customer alerts, webhook delivery of alerts, evidence pictures in Cloudflare R2 with expiring links,
delivery guarantees and limits. Tests: `tests/test_webhook_unit.py` (payload, signature, address guard, real TLS round trip),
`tests/test_alert_catalogue.py` (who sees which alert) and `tests/test_alert_delivery.py` (the whole journey, retries, resend, limits, isolation).


## Data retention

Each tenant keeps events (footfall, plate reads, alerts and their message log) for `retention_events_days` (default 30, up to 730) and
pictures and clips for `retention_snapshots_days` (default 7, up to 90), both set in the admin console. A clean-up runs every six hours
(`services/retention.py`): it deletes in batches of 5,000 rows with a two-minute budget per run (an unfinished job continues next run),
deletes the stored file before the database row, and drops whole plate-read months older than 730 days. Never deleted: the platform audit
log, tenants' settings and people. The owner can run it on demand (`POST /admin/v1/maintenance/retention/run`, audited); everyone on
staff can see what it did (`GET /admin/v1/maintenance/runs`). Deleting a cancelled tenant's data is not built.

## Keyframe cache in the video relay

A viewer who joins a stream part-way is first given the video since the latest keyframe (`registrar/gop.py`), then the live bytes, so it
starts at once instead of waiting one to four seconds for the next keyframe. Recognised for H.264 program streams only; other codecs and
recorders with a keyframe interval longer than 3 MB of video fall back to joining live. `GI_REG_RELAY_GOP_CACHE_BYTES=0` turns it off.
Tested with synthetic program streams (every chunking, split start codes, overflow, several IDR slices) and through the relay; not yet seen
with a real recorder's stream, so a recorder that packs keyframes differently simply gets the old behaviour.


## Reports (Excel)

Three reports, made on demand or on a schedule, always for whole local days of the customer's own data:

| Report | Sheets | Longest period |
|---|---|---|
| Gate register | every plate read (date, time, plate, vehicle, gate, direction, time inside, list) and a daily summary | 31 days |
| Footfall summary | by day and camera, by day, by hour of day | 366 days |
| Vehicle audit | each vehicle's visits and time inside, and who is still inside | 92 days |

* `POST /v1/reports` (administrator) queues one (`202`); a background job builds it (`FOR UPDATE SKIP LOCKED`, a crashed builder's report is
  picked up again after 10 minutes); `GET /v1/reports/{id}/download` gives the file. At most 5 waiting and 20 requested a day per customer.
* `POST /v1/report-schedules`: daily, weekly (a weekday) or monthly (day 1 to 28) at a local time, covering the day, the 7 days, or the
  previous calendar month before it. Each period is produced once; after downtime only the newest missed one. A new or changed schedule
  first fires at its next time. Scheduled reports are **emailed with the file attached** (a file over 8 MB is mentioned, not attached).
* Files are kept for the tenant's event retention period, then deleted with the retention job.
* Text in a file is always written as text, so a camera name starting with `=` can never run as a formula.
* Not built: sending a report by WhatsApp (needs its own approved template), PDF output, custom columns.

## Many cameras in one worker process

```bash
python -m worker run-many --shard 0 --of 1            # every runnable camera, discovered from the backend
python -m worker run-many --shard 1 --of 4 --parallel 3   # a quarter of them, three pictures analysed at once
python -m worker run-many --camera-ids <id> <id>      # exactly these
```

* The backend lists the cameras a worker should run (`GET /internal/v1/workers/cameras?shard=&of=`): switched on, something to analyse, a
  stream address (or a registered recorder), account active. Cameras are split between `of` processes by a stable hash, so each camera has
  exactly one worker. The process asks again every minute: cameras added later start, switched-off ones stop, one whose runner ended is
  started again.
* Each camera keeps its own settings, frame rate, health reports, reconnect handling and clip buffer. The detector and plate models and the
  sender to the backend are shared. Analysis runs in threads, at most `--parallel` at a time (default: `GI_WORKER_THREADS`), so one camera's
  analysis never stalls another camera's health report or reconnect.
* One camera the backend refuses stops alone; its waiting data is dropped, everyone else continues. A camera that crashes is logged and
  restarted at the next poll without touching the rest.
* **Capacity is not measured here.** Detection costs real CPU per picture; how many cameras one process holds depends on the machine, the
  frame rates set in the admin console, and how busy the scenes are. Start with a few cameras per core, watch the "falling behind" figure
  in the admin console (dropped pictures), and spread cameras over more processes with `--shard/--of`.
* Not built: moving a camera between workers while it runs (it restarts on the new one), and a shared plate-reader queue across processes.


## Cameras from an RTSP address

For recorders that cannot register with us (no GB28181), a technician or the owner creates a camera from its RTSP address:
`POST /admin/v1/rtsp-check` opens the address from the server and says what it found, and `POST /admin/v1/tenants/{id}/rtsp-cameras`
creates the camera (plan limits and features apply; the address is checked first unless `check_first` is false). The address is stored
encrypted, bound to the camera, never returned and never written to the audit log. Addresses pointing at the server itself are refused;
private and VPN addresses are allowed. Such a camera has `connection: "rtsp"` and no recorder; everything else (zones, frame rate,
alerts, reports, billing) treats it like any camera. Not built: a customer-facing way to add one, and probing from the worker's own network
instead of the API server's.
