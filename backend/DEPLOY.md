# First real-hardware test on a VPS

Goal: one real recorder (your Dahua DHI-NVR4216-4KS2/L) registers with a server on the internet, its channels show up, and live video
reaches the analysis worker. This is a **test setup**, not production. The last section lists what is missing for production.

Nothing here has been run on a real server or a real recorder yet. Where a step depends on your recorder's menu, it says so.

## 0. What you need

- [ ] A VPS with a **public IPv4 address**. 2 vCPU / 4 GB RAM is enough for one or two cameras. Ubuntu 24.04 LTS assumed.
- [ ] The recorder on a network with internet access, its **clock set correctly** (India time), and its admin password.
- [ ] A way to open the recorder's menu (its screen and mouse, or its web page).
- [ ] (Optional for the first test) a domain name. Without one, use the VPS IP address everywhere.
- [ ] (Optional for the first test) Cloudflare R2 bucket and keys. Without them, evidence pictures are stored on the VPS disk. Set R2 up in the owner console under Storage (no file edits or restart); the `GI_EVIDENCE_*` and `GI_R2_*` lines in `.env` apply only until you save settings there.

## 1. Prepare the server

- [ ] Create a non-root user and log in with an SSH key; turn off password login for SSH.
- [ ] `sudo apt update && sudo apt install -y postgresql-16 ffmpeg python3-venv python3-pip git ufw nginx`
      (Python 3.13 was used for development; if `python3 --version` is older, install 3.13 first. Older versions are **not tested**.)
- [ ] Copy the project to the server (`/opt/gateinsight`), then:
      ```bash
      cd /opt/gateinsight/backend
      python3 -m venv .venv && source .venv/bin/activate
      pip install -r requirements.txt -r requirements-worker.txt
      python -m scripts.fetch_models        # downloads the detector and plate models (needs internet, checks checksums)
      ```
- [ ] Set the server clock: `timedatectl set-ntp true`.

## 2. Firewall: open exactly these, nothing else

| Port | Protocol | Who needs it | Why |
|---|---|---|---|
| 22 | TCP | you | SSH (limit to your own IP if you can) |
| 5060 | **UDP and TCP** | the recorder | SIP: the recorder registers here |
| 30000–30999 | **UDP** | the recorder | its video arrives on one of these ports |
| 80, 443 | TCP | browsers (later) | the web API, only if you put it behind nginx |

- [ ] `sudo ufw default deny incoming && sudo ufw allow 22/tcp && sudo ufw allow 5060/udp && sudo ufw allow 5060/tcp && sudo ufw allow 30000:30999/udp && sudo ufw enable`
- [ ] **Do not open** 5432 (PostgreSQL), 8000 (API) or 8081 (video relay). They stay on `127.0.0.1`.
- [ ] If the VPS provider has its own firewall or "security group" (AWS, Azure, Oracle, DigitalOcean, ...), open the same ports **there too**. This is the most common reason a recorder never connects.
- [ ] On a cloud server whose public address is not on its network card (AWS, Oracle, GCP), that is fine: use the public address in `GI_REG_PUBLIC_IP` below.

## 3. Database

- [ ] ```bash
      sudo -u postgres createdb gateinsight
      sudo -u postgres psql gateinsight -f /opt/gateinsight/schema.sql
      sudo -u postgres psql gateinsight -c "CREATE ROLE gi_api LOGIN PASSWORD '<a long random password>'; GRANT app_user, platform_admin, service_worker TO gi_api; GRANT CONNECT ON DATABASE gateinsight TO gi_api;"
      ```
- [ ] PostgreSQL listens on `localhost` only (the default). Check: `ss -ltn | grep 5432` shows `127.0.0.1`.
- [ ] Back it up before the test ends: `sudo -u postgres pg_dump gateinsight > backup.sql`.

## 4. Configuration (`/opt/gateinsight/backend/.env`)

Generate every secret fresh. Never reuse one for two purposes (the app refuses if the three below are equal).

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"                       # run three times
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"     # for the encryption key
```

```ini
GI_ENV=dev                                   # dev turns on /docs, which you will use through an SSH tunnel (section 7). Never expose it publicly.
GI_DATABASE_URL=postgresql://gi_api:<password>@127.0.0.1:5432/gateinsight
GI_JWT_SECRET_CUSTOMER=<secret 1>
GI_JWT_SECRET_PLATFORM=<secret 2>
GI_SERVICE_TOKEN=<secret 3>
GI_KEY_ENCRYPTION_KEY=<base64 key>           # losing it makes stored recorder keys and camera passwords unreadable: keep a copy somewhere safe
GI_REGISTRAR_HOST=<VPS public IP or domain>  # what customers are told to type into the recorder
GI_GB_REALM=3402000000                       # these three must equal the registrar's values below
GI_GB_SERVER_ID=34020000002000000001
GI_GB_PORT=5060
GI_PUBLIC_BASE_URL=http://<VPS IP or domain>
GI_EVIDENCE_BACKEND=local                    # switch to r2 later

# registrar (same file, GI_REG_ prefix)
GI_REG_BACKEND_URL=http://127.0.0.1:8000
GI_REG_SERVICE_TOKEN=<secret 3, identical to GI_SERVICE_TOKEN>
GI_REG_PUBLIC_IP=<VPS public IPv4>
GI_REG_REALM=3402000000
GI_REG_SERVER_ID=34020000002000000001
GI_REG_RTP_PORT_MIN=30000
GI_REG_RTP_PORT_MAX=30999

# worker
GI_WORKER_BACKEND_URL=http://127.0.0.1:8000
GI_WORKER_RELAY_URL=http://127.0.0.1:8081
GI_WORKER_SERVICE_TOKEN=<secret 3, identical>
```

- [ ] `chmod 600 .env`. Keep it out of git.
- [ ] The realm and server id above are the standard example values (they identify our server to the recorder; they are not secrets).

## 5. Run the three services (systemd)

Create `/etc/systemd/system/gi-api.service`, `gi-registrar.service`, `gi-worker.service`. Same pattern for each:

```ini
[Unit]
Description=Jas iVision API            # registrar: "... registrar"   worker: "... worker"
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=gateinsight
WorkingDirectory=/opt/gateinsight/backend
EnvironmentFile=/opt/gateinsight/backend/.env
ExecStart=/opt/gateinsight/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# registrar:  ExecStart=/opt/gateinsight/backend/.venv/bin/python -m registrar
# worker:     ExecStart=/opt/gateinsight/backend/.venv/bin/python -m worker run-many --shard 0 --of 1
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

- [ ] Start in this order: `sudo systemctl enable --now gi-api`, then `gi-registrar`, then (after step 8) `gi-worker`.
- [ ] Check the API: `curl -s http://127.0.0.1:8000/healthz` → `{"ok":true}`.
- [ ] Check the registrar is listening: `sudo ss -lunp | grep 5060` and `sudo ss -ltnp | grep 5060`.
- [ ] Logs: `journalctl -u gi-api -f`, `journalctl -u gi-registrar -f`.

## 6. Create the test customer and tokens

Sign-in exists in the API (`/v1/auth/login`, `/admin/v1/auth/login`); the web pages for it come next. Until then you can still use tokens printed by the seed script. See section 6b to create a real super admin.

- [ ] ```bash
      cd /opt/gateinsight/backend && source .venv/bin/activate
      GI_SEED_DSN=postgresql://postgres@/gateinsight python -m scripts.seed_dev      # run as the postgres user: sudo -u postgres -E ...
      ```
- [ ] It prints staff tokens and a customer admin token (valid 8 hours). Save the **customer admin token** and the **technician token**.
- [ ] The seed creates a customer "Sharma Auto Components" on the `full` plan with room for 10 cameras.

## 6b. Real sign-in: the first super admin

1. [ ] Apply the database change once (not needed for a database created from the current `schema.sql`):
   ```bash
   cd /www/wwwroot/iva/backend && su postgres -c "psql -d gateinsight -f migrations/002_auth.sql"
   su postgres -c "psql -d gateinsight -f migrations/003_recorder_name.sql"
   su postgres -c "psql -d gateinsight -f migrations/004_platform_console.sql"
   su postgres -c "psql -d gateinsight -f migrations/005_storage_and_plate_pictures.sql"
   su postgres -c "psql -d gateinsight -f migrations/006_ai_view.sql"
   su postgres -c "psql -d gateinsight -f migrations/007_alert_catalogue.sql"
   su postgres -c "psql -d gateinsight -f migrations/008_alert_webhooks.sql"      # removes the old WhatsApp provider and templates: set up webhooks in the staff console afterwards
   su postgres -c "psql -d gateinsight -f migrations/009_system_alert_data.sql"   # connection history and known sign-in devices
   su postgres -c "psql -d gateinsight -f migrations/010_area_alerts.sql"         # marked areas; the area alerts start switched off
   ```
   Then restart the API: `systemctl restart gi-api`.
2. [ ] Create your own staff account. It asks for a password (hidden, typed twice) and prints a setup key for an authenticator app:
   ```bash
   cd /www/wwwroot/iva/backend && .venv/bin/python -m scripts.create_staff --email you@yourcompany.in --name "Your Name" --role owner
   ```
   Add the key to Google Authenticator, Microsoft Authenticator or Authy. Running it again for the same email resets the password and the authenticator and signs that person out everywhere.
3. [ ] Check it (use a hidden prompt, do not paste the password into chat):
   ```bash
   read -rs P; curl -s -X POST http://127.0.0.1:8000/admin/v1/auth/login -H "Content-Type: application/json" -d "{\"email\":\"you@yourcompany.in\",\"password\":\"$P\",\"code\":\"123456\"}"
   ```
   Replace `123456` with the code your app shows. You get an `access_token`.
### Signing in without an authenticator app (optional, weaker)

By default staff need their password **and** a 6-digit code. To use the password alone, add `GI_STAFF_REQUIRE_MFA=false` to `.env` and restart `gi-api`:

```bash
grep -q '^GI_STAFF_REQUIRE_MFA=' /www/wwwroot/iva/backend/.env && sed -i 's|^GI_STAFF_REQUIRE_MFA=.*|GI_STAFF_REQUIRE_MFA=false|' /www/wwwroot/iva/backend/.env || echo 'GI_STAFF_REQUIRE_MFA=false' >> /www/wwwroot/iva/backend/.env; systemctl restart gi-api
```

The sign-in page then hides the code box by itself. Things to know:
- **It is weaker.** The staff console can see every customer, invite users and view as a customer. With the code, a stolen or guessed password is not enough; without it, it is. Use a long, unique password (a few words together), and do not reuse it anywhere.
- Limits stay: five wrong passwords lock the account for 15 minutes, and attempts per address are limited.
- Tokens made while it is off are marked "password only". If you turn it back on (`GI_STAFF_REQUIRE_MFA=true`, or remove the line), those sessions stop working at once and must sign in again with a code, so an old password-only session can never be upgraded.
- `scripts.create_staff --no-authenticator` creates an account without an authenticator key. An account that already has one keeps working with or without it.

4. [ ] Invite a customer's first user: `POST /admin/v1/tenants/<tenant id>/users` with `{"email","name","role":"admin"}` returns a `link`. Send it to the person; they choose their own password there.

## 6c. The web pages

The API serves the pages itself, so there is nothing else to install: no Node, no build step.

| Address | Who | What |
|---|---|---|
| `/` | customers | the customer app: sign in, home, cameras (live view, counting line, plate area), AI Watch, alerts, reports, activity, settings, recorders |
| `/staff/` | your team | sign in (password + authenticator code), overview, customers, invite users, add recorders, camera health |
| `/set-password?token=...` | invited customers | the one-time link staff send; they choose their own password |

- [ ] Set the public address in `.env` so invitation links point to the right place: `GI_PUBLIC_BASE_URL=https://app.yourcompany.in` (until you have the domain, use the tunnel address below). Restart `gi-api`.
- [ ] Until the domain and HTTPS are ready, reach the pages through an SSH tunnel from your laptop: `ssh -L 8000:127.0.0.1:8000 you@<VPS IP>`, then open `http://localhost:8000/staff/` (and `http://localhost:8000/` for the customer side). Set `GI_PUBLIC_BASE_URL=http://localhost:8000` meanwhile. **Do not put the pages on the open internet over plain HTTP: passwords would travel unprotected.**
- [ ] With the domain: in aaPanel add a website for the domain, turn on its free Let's Encrypt certificate, and set it up as a **reverse proxy** to `http://127.0.0.1:8000`. Keep the default `X-Forwarded-*` headers. The API then adds `Strict-Transport-Security` by itself.
- [ ] Set `GI_ENV=prod` once real customers use it: the interactive API pages (`/docs`) are then switched off.
- [ ] Staff pages: sign in with the account from section 6b. In the console: **Customers → New customer → Invite a user**, copy the link, send it to the customer (for example on WhatsApp).
- [ ] Taking a camera picture (the "draw your counting line" screen) needs FFmpeg on the server (`ffmpeg -version`) and the Dahua gateway or registrar running. Settings if they are elsewhere: `GI_DAHUA_RELAY_URL` (default `http://127.0.0.1:8082`), `GI_GB_RELAY_URL` (default `http://127.0.0.1:8081`), `GI_FFMPEG_PATH`.

**The customer app** (Flutter, built from `app/` by `scripts/build_app.sh` into `backend/web_next`) is served at the plain address. The old customer pages are gone. The staff console is still at `/staff/`. Old addresses keep working: `/next/...` redirects to `/`, and the old app's cached helper (`/sw.js`) is answered with one that empties its caches and removes itself, so phones that still hold the old app move over by themselves. A staff member who signs in on the customer page is handed on to the staff console, and "open the customer's app" from the console opens the customer view-only in the same app.

Live view (moving pictures) uses FFmpeg on the server and keeps a connection open while someone watches. If you put a proxy in front, turn response buffering off for `/v1/live/` (the API already sends `X-Accel-Buffering: no`, which nginx honours). `GI_LIVE_STREAM_MAX` (default 8) is how many cameras can be watched at once on one server; `GI_LIVE_STREAM_FPS` (default 6) is the pictures a second. Push notifications are in `docs/PUSH.md`.

Security of the pages: they load only their own scripts and styles (a strict content policy is sent with every page), they never put server or customer text into the page as HTML, and the sign-in token lives in the browser only: customers keep a long-lived refresh token (so they stay signed in on their own phone), staff keep theirs until the tab is closed.

## 7. Reach the API safely

- [ ] From your laptop: `ssh -L 8000:127.0.0.1:8000 you@<VPS IP>` and leave it open. Now `http://localhost:8000/docs` on your laptop is the API's interactive page. The page cannot send your token for you, so use curl with the header as in section 8.
- [ ] Nothing about the API needs to be public for this test.

## 8. Register the recorder (the real test)

1. [ ] Ask for credentials. **For Dahua, section 10b (Auto Register) is the main route. The GB28181 route is staff-only: customers cannot ask for it, so use a staff (owner) token and the tenant's id:**
   ```bash
   curl -s -X POST http://localhost:8000/admin/v1/tenants/<tenant id>/recorders \
     -H "Authorization: Bearer <staff owner token>" -H "Content-Type: application/json" \
     -d '{"brand":"dahua","protocol":"gb28181","reason":"This model has no Auto Register page"}'
   ```
   The answer is shown **once**: `server`, `port`, `server_id`, `domain`, `device_id` (20 digits), `key`. Copy it now.
2. [ ] On the recorder, open the menu that points it at a server. Look under **Network** (or Network → Advanced / Platform Access) for **GB28181**.
   The name differs by model and firmware. **If there is no GB28181 page, stop here** and tell me what the Network menu does list (a photo helps).
   A page called only "Auto Register" is Dahua's own protocol: use section 10b.
3. [ ] Enter:

   | Recorder field | Value |
   |---|---|
   | Enable | on |
   | SIP server IP / address | `server` from the answer |
   | SIP server port | `port` (5060) |
   | SIP server ID | `server_id` |
   | SIP domain / realm | `domain` |
   | Device ID / username | `device_id` |
   | Password | `key` |
   | Register validity | 3600 s |
   | Heartbeat | 60 s |
   | Transport | UDP (try TCP only if UDP fails) |
   | Channel IDs / "video channel code" | if the page asks for per-channel IDs, leave the defaults first; the server reads the list from the recorder |

4. [ ] Save. Within a minute, on the VPS: `journalctl -u gi-registrar -f` should show `recorder registered device=<your device id>`.
5. [ ] Check status: `curl -s -H "Authorization: Bearer <customer admin token>" http://localhost:8000/v1/recorders/<recorder_id>/status` → `status` becomes `online`; `GET /v1/recorders/<recorder_id>/channels` lists the channels the recorder reported.
6. [ ] Create a camera for one channel (`POST /v1/cameras` with `recorder_id`, `channel_no`, `name`, and `use_footfall` or `use_anpr`), draw its counting line or plate zone (`PUT /v1/cameras/<id>/zones`).
7. [ ] Start the worker: `sudo systemctl enable --now gi-worker`. Within about a minute `journalctl -u gi-worker -f` shows the camera starting, and `GET /admin/v1/tenants/<id>/cameras` (technician token) shows `stream_state: streaming`.
8. [ ] Walk in front of the camera. `GET /v1/footfall/summary` (or `/v1/plates` for a vehicle) shows the count.

## 9. If something does not work

| Symptom | Check |
|---|---|
| Nothing in the registrar log | `sudo tcpdump -ni any udp port 5060` on the VPS while you press Save on the recorder. **No packets** = a firewall (the VPS provider's too), a wrong server address, or the recorder cannot reach the internet. **Packets but no registration** = wrong device id, key, realm or server id (compare with the answer from step 8.1). |
| `403` / repeated attempts in the log | Wrong key or device id. The server answers every failure the same way on purpose. Ask for a new key (`POST /v1/recorders/<id>/rotate-key`) and re-enter it. 10 failed tries in a minute from one address are ignored for a while. |
| Registers, then goes offline after about 3 minutes | Keepalives not arriving: recorder heartbeat setting, or UDP being dropped by the customer's router. Try TCP transport. |
| Online, but the camera never reaches `streaming` | The video path. `sudo tcpdump -ni any udp portrange 30000-30999` while the worker starts: **no packets** = the 30000–30999/UDP range is blocked (provider firewall again) or the recorder cannot send to it. Try `GI_REG_RELAY_MEDIA_TRANSPORT=tcp` if the recorder offers TCP media. Check `GI_REG_PUBLIC_IP` is the address the recorder can actually reach. |
| `ffmpeg` errors in the worker log | Run `ffmpeg -version`; the worker needs FFmpeg (6.1 was used in development; other versions are not tested). |
| Counts stay zero though video is streaming | Zones not drawn, or the line is not where people cross. Footfall needs a counting line; plates need a plate zone. Look at `GET /v1/cameras/<id>/zones`. |

When reporting a problem, send: the recorder model and firmware version, a photo of its GB28181 page (hide the password), the last 50
lines of `journalctl -u gi-registrar`, and the tcpdump result. That is usually enough to tell which of the above it is.

## 10. Fallback if the recorder has no GB28181 page: an RTSP camera

Use a direct RTSP address (the registrar is not involved). The VPS must be able to reach the recorder: a VPN between the site and the
VPS (safest), or a port forward on the site router plus the recorder's IP filter allowing only the VPS address. Never expose a recorder
without both a strong password and an IP filter.

1. [ ] On the recorder, create a **viewer-only user** (live view only) and use that, not `admin`.
2. [ ] Dahua channels are usually `rtsp://<user>:<password>@<address>:554/cam/realmonitor?channel=N&subtype=1` (`subtype=1` is the lighter second stream, which is what analysis should use). Hikvision is usually `rtsp://<user>:<password>@<address>:554/Streaming/Channels/N02`. Test the address in VLC on the site network first.
3. [ ] Check it from the server (nothing is saved, and the address is not repeated in the answer):
   ```bash
   curl -s -X POST http://localhost:8000/admin/v1/rtsp-check -H "Authorization: Bearer <technician token>" -H "Content-Type: application/json" \
     -d '{"url":"rtsp://viewer:PASSWORD@ADDRESS:PORT/cam/realmonitor?channel=1&subtype=1"}'
   ```
   `"ok": true` with a picture size means the server can see the camera. Otherwise `problem` says why (wrong password, nothing listening, no answer).
4. [ ] Create the camera for the customer (technician or owner token). The address is checked again, then stored encrypted and never shown again:
   ```bash
   curl -s -X POST http://localhost:8000/admin/v1/tenants/<tenant id>/rtsp-cameras -H "Authorization: Bearer <technician token>" -H "Content-Type: application/json" \
     -d '{"name":"Main gate","url":"rtsp://viewer:PASSWORD@ADDRESS:PORT/cam/realmonitor?channel=1&subtype=1","use_footfall":true,"reason":"Customer has a static IP"}'
   ```
5. [ ] Draw its counting line or plate zone (`PUT /v1/cameras/<id>/zones`), then start the worker. It picks the camera up within a minute.

Addresses that point at the server itself (127.x, localhost, 169.254.x) are refused. Private network and VPN addresses are allowed.

**A server installed before this feature** needs a one-time database change first, run as the database owner:
```bash
sudo -u postgres psql -d gateinsight -f /www/wwwroot/iva/backend/migrations/001_rtsp_cameras.sql
```
Then `git pull` and `systemctl restart gi-api`.

## 10b. Dahua "Auto Register" (the main route for Dahua recorders)

The recorder connects OUT to our server, so the customer needs no VPN and no port forwarding. A separate service, the **Dahua gateway**
(`python -m dahua`), accepts these connections. It uses Dahua's own NetSDK, which is **not in this repository**: you download it from Dahua
(Python package for Linux 64-bit), accept Dahua's licence, and install the `.whl` into the venv. Check the licence allows hosted use for your customers.

1. [ ] Install the wrapper into the service venv, then check it loads **from a folder that is not the wrapper's own**:
   ```bash
   /opt/gateinsight/backend/.venv/bin/pip install /root/sdk/whl/NetSDK-*.whl      # path to the .whl you downloaded
   cd /tmp && /opt/gateinsight/backend/.venv/bin/python -c "from NetSDK.NetSDK import NetClient; print('ok')"
   ```
   (A line `动态库加载失败` at start-up has been seen on a working setup: it is Dahua's library telling us one optional part did not load.)
2. [ ] Open the port: `sudo ufw allow 7000/tcp`. Add to `.env`: `GI_DH_SERVICE_TOKEN` (the same value as `GI_REG_SERVICE_TOKEN`) and, if the API is not on 127.0.0.1:8000, `GI_DH_BACKEND_URL`. Add `GI_WORKER_DAHUA_URL=http://127.0.0.1:8082` for the worker if it runs elsewhere.
3. [ ] `/etc/systemd/system/gi-dahua.service`: same as the registrar's, with `ExecStart=/opt/gateinsight/backend/.venv/bin/python -m dahua`. `sudo systemctl enable --now gi-dahua`, then `journalctl -u gi-dahua -f` shows `dahua gateway listening`.
4. [ ] Create the recorder **without** saying gb28181 (a Dahua recorder defaults to Auto Register): `POST /v1/recorders` with `{"brand":"dahua"}`. The answer gives `server`, `port` (7000), `device_id`, `key` and `viewer_user`.
5. [ ] On the Dahua recorder:
   - Network → Register: Enable, Server Address = the VPS public IP, Port = 7000, **Sub-Device ID = the `device_id`** (not `0`).
   - Account (user management): add a user named `viewer_user` (default `giview`), password = the `key`, **viewer-only permissions** (live view only), never the admin account.
6. [ ] `journalctl -u gi-dahua -f` shows `recorder <device_id> online from <ip> with N channels`. Status and channels then appear through the API exactly as for GB28181.
7. [ ] Create cameras and start the worker as in section 8. A camera's `stream` setting picks the main stream (full resolution: use this for number plates) or the sub stream (light: enough for counting people).

Things to know:
- The recorder is recognised by the Sub-Device ID **and** the key: a wrong key never gets in. After a refused sign-in the gateway ignores that recorder for 30 s, doubling to 15 min, so a mistyped password does not lock the viewer account.
- If the ID on the recorder is left at `0` the gateway ignores the registration and logs `registration from <ip> with an id we do not serve`. Fix the Sub-Device ID.
- If the Dahua page refuses the key as a password (some firmware wants a mix of letters and digits), rotate the key (`rotate-key`) until it is accepted.
- Video is Dahua's own container (DHAV, H.265 or H.264). The worker reads it with FFmpeg's `-f dhav`.

## 11. After the test

- [ ] Rotate or delete the test recorder key; revoke the seed tokens by changing `GI_JWT_SECRET_*` and restarting.
- [ ] `sudo systemctl stop gi-worker gi-registrar gi-api` if you will not continue (the VPS is still reachable on 5060 until the firewall rule is removed).

## What is **not** ready for real customers

These are gaps in the product itself, not in this checklist:

1. **Sign-in pages and password email.** The sign-in API is built (passwords, authenticator codes for staff, invitations, reset links) but there are no web pages for it yet, and no self-service "forgot password" email: staff send reset links by hand.
2. **No web app.** The screens exist only as mock-ups. Everything is driven through the API.
3. **Never run against a real recorder, real WhatsApp provider or R2** (this test is the first for the recorder).
4. **HTTPS and hardening:** the API is plain HTTP on localhost here. Before any customer: nginx with a real certificate, `GI_ENV=production`, CORS set, rate limiting at nginx, automatic OS updates, off-server backups of the database and of `GI_KEY_ENCRYPTION_KEY`.
5. **Monitoring:** nothing alerts you if the registrar or worker dies (systemd restarts them, but you are not told).
6. **Invoicing** is built but paused: not reviewed for GST rules, no screens.
7. **Hikvision is not supported** (the product connects Dahua recorders only). Dahua Auto Register is built (section 10b) but tested only with one NVR model; GB28181 and RTSP also work as fallbacks that staff set up.
