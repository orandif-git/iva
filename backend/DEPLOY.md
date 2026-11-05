# First real-hardware test on a VPS

Goal: one real recorder (your Dahua DHI-NVR4216-4KS2/L) registers with a server on the internet, its channels show up, and live video
reaches the analysis worker. This is a **test setup**, not production. The last section lists what is missing for production.

Nothing here has been run on a real server or a real recorder yet. Where a step depends on your recorder's menu, it says so.

## 0. What you need

- [ ] A VPS with a **public IPv4 address**. 2 vCPU / 4 GB RAM is enough for one or two cameras. Ubuntu 24.04 LTS assumed.
- [ ] The recorder on a network with internet access, its **clock set correctly** (India time), and its admin password.
- [ ] A way to open the recorder's menu (its screen and mouse, or its web page).
- [ ] (Optional for the first test) a domain name. Without one, use the VPS IP address everywhere.
- [ ] (Optional for the first test) Cloudflare R2 bucket and keys. Without them, evidence pictures are stored on the VPS disk (`GI_EVIDENCE_BACKEND=local`).

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
Description=Gate Insight API            # registrar: "... registrar"   worker: "... worker"
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

There is **no sign-in screen or login API yet** (see the last section). For the test you use tokens printed by the seed script.

- [ ] ```bash
      cd /opt/gateinsight/backend && source .venv/bin/activate
      GI_SEED_DSN=postgresql://postgres@/gateinsight python -m scripts.seed_dev      # run as the postgres user: sudo -u postgres -E ...
      ```
- [ ] It prints staff tokens and a customer admin token (valid 8 hours). Save the **customer admin token** and the **technician token**.
- [ ] The seed creates a customer "Sharma Auto Components" on the `full` plan with room for 10 cameras.

## 7. Reach the API safely

- [ ] From your laptop: `ssh -L 8000:127.0.0.1:8000 you@<VPS IP>` and leave it open. Now `http://localhost:8000/docs` on your laptop is the API's interactive page. The page cannot send your token for you, so use curl with the header as in section 8.
- [ ] Nothing about the API needs to be public for this test.

## 8. Register the recorder (the real test)

1. [ ] Ask for credentials. **Dahua defaults to a protocol we have not built, so you must say `gb28181`:**
   ```bash
   curl -s -X POST http://localhost:8000/v1/recorders \
     -H "Authorization: Bearer <customer admin token>" -H "Content-Type: application/json" \
     -d '{"brand":"dahua","protocol":"gb28181"}'
   ```
   The answer is shown **once**: `server`, `port`, `server_id`, `domain`, `device_id` (20 digits), `key`. Copy it now.
2. [ ] On the recorder, open the menu that points it at a server. Look under **Network** (or Network → Advanced / Platform Access) for **GB28181**.
   The name differs by model and firmware. **If there is no GB28181 page, stop here** and tell me what the Network menu does list (a photo helps).
   A page called only "Auto Register" is Dahua's own protocol, which is **not** built.
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

## 11. After the test

- [ ] Rotate or delete the test recorder key; revoke the seed tokens by changing `GI_JWT_SECRET_*` and restarting.
- [ ] `sudo systemctl stop gi-worker gi-registrar gi-api` if you will not continue (the VPS is still reachable on 5060 until the firewall rule is removed).

## What is **not** ready for real customers

These are gaps in the product itself, not in this checklist:

1. **No sign-in.** There is no login, password, OTP or invite flow for customers or staff. Access is by tokens made with a script. A real sign-in is the biggest missing core piece.
2. **No web app.** The screens exist only as mock-ups. Everything is driven through the API.
3. **Never run against a real recorder, real WhatsApp provider or R2** (this test is the first for the recorder).
4. **HTTPS and hardening:** the API is plain HTTP on localhost here. Before any customer: nginx with a real certificate, `GI_ENV=production`, CORS set, rate limiting at nginx, automatic OS updates, off-server backups of the database and of `GI_KEY_ENCRYPTION_KEY`.
5. **Monitoring:** nothing alerts you if the registrar or worker dies (systemd restarts them, but you are not told).
6. **Invoicing** is built but paused: not reviewed for GST rules, no screens.
7. **Hikvision ISUP and Dahua Auto Register** are not built; GB28181 is the only recorder protocol that works.
