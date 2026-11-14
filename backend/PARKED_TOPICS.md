# Parked topics

Things we looked into and set aside on purpose, with what we already know, so they can be picked up later. To resume one, say its label (for example "resume HIK-BACKUP").

## HIK-BACKUP: a backup path for Hikvision (and other brands') recorders

**Status:** parked, waiting for more details from the owner. Nothing is built yet. Hikvision is still not a supported brand: Hikvision recorders stay on Hik-Connect (see README).

**The idea:** a generic backup when a shop's recorder cannot be a Dahua auto-register recorder. Two ways in, and what each gives:

| Way | Needs | Gives |
|---|---|---|
| **A. Alert push** (the recorder sends fault alerts to a private address on our server) | Internet access from the recorder; nothing in the shop's router; a receiver on our API (not built) | Fault alerts: video loss, disk full, disk error, network down, wrong sign-in. Not a full health screen. Keeps Hik-Connect on. |
| **B. VPN** (WireGuard from the shop into our server) | Our server can reach the recorder; per-shop private address ranges that do not clash | Full health with plain web requests (`/ISAPI/ContentMgmt/Storage`, `/ISAPI/System/status`, `/ISAPI/System/time`), events by keeping `/ISAPI/Event/notification/alertStream` open, and video by RTSP (the camera-by-address backup). |
| **C. Hikvision toolkits** (OTAP, ISUP Device Gateway, Device Network SDK) | Partner licence and terms, a gateway written and tested | Possibly full support later. Not for now. |

**What we found on the test recorder (DS-7616NXI-K1)**
- ISAPI answers over the web with digest sign-in. Alert destination slot 1 exists at `/ISAPI/Event/notification/httpHosts` (empty: HTTP, port 80, XML, no authentication). The web UI has no screen for it, so it would be filled with one command.
- Platform Access tabs in its web UI: OTAP, ISUP, SDK, ISAPI (only an Enable switch), ONVIF, Cloud Storage, Log Server (syslog). Switching **ISUP on switches Hik-Connect off**, so do not use ISUP on a shop that uses the Hik-Connect app.
- Hikvision's site says the OTAP developer guide is not available for this model, so treat OTAP as not usable here. The ISAPI Developer Guide does list this model. A free Device Gateway (ISUP) for Linux64 and the Device Network SDK exist.
- Event message format (arming mode, same parts expected in listening mode): multipart (mixed or form-data); the XML part is `EventNotificationAlert` with `ipAddress`, `portNo`, `macAddress`, `channelID`, `dateTime` (with +05:30), `eventType`, `eventState` (`active` or `inactive`), `eventDescription`; `heartBeat` events keep the link alive; picture parts may follow; some events can be JSON, so look at each part's content type. Seen so far: `videoloss` with `inactive` (the camera came back).

**WireGuard notes (TP-Link Archer AX55 V1)**
- Official WireGuard firmware for AX55 V1 (June 2023). Its "VPN Client" is built to send the shop's traffic out, so reaching a device inside the shop from our server is unproven: test by pinging the router's tunnel address, then the recorder's address, from the server. A small always-on device in the shop is the fallback.
- Server side: `apt install wireguard` (Debian 13), keep encrypted DNS off while testing, open UDP 51820 in the Baota and hosting firewalls, allowed addresses only the tunnel range (not 0.0.0.0/0), one key per shop, unique private range per shop.

**Second test recorder (DS-7632NXI-K2, firmware V4.84.100) - results, 14 Nov 2026**
- Sign-in over the web works (digest). `ContentMgmt/Storage` works: it showed disk 1 as `notexist` with capacity 0 (no hard disk fitted in this unit), so a missing or failed disk is visible this way. `System/status` gives only uptime and memory (no disk, camera or CPU detail). `System/time` works (time comes from the platform, India time).
- `subscribeEventCap` lists: diskfull, diskerror, illaccess, videoloss, IO, shelteralarm, VMD, recordException, hotSpare, line and field detection, region entrance and exit, loitering, parking, facedetection, faceCapture, ANPR, personDensityDetection, defocus, scenechangedetection and more. What actually fires depends on the cameras. No people-counting event is listed. Capabilities show face detect, face snap, human and vehicle target types; human recognition is false.
- `httpHosts` slot 1 is empty, so way A (alert push) is possible on this model.
- `alertStream` repeated `videoloss` `inactive` for channel 0 about every 9 seconds. No heartBeat parts, no `active` message yet (no camera was unplugged during the test). The repeating message works as a liveness signal.
- Still to do: repeat the live-event test with a camera unplugged for about 30 seconds; read `ContentMgmt/InputProxy/channels/status` (which cameras are online).

**Open items (to come from the owner)**
1. The full `alertStream` output: the `active` video-loss message, any other `eventType` (disk full, disk error, network, illegal login), and which camera was unplugged.
2. The output of `/ISAPI/Event/notification/subscribeEventCap` and the face, counting and human parts of `/ISAPI/System/capabilities` (people counting and face events depend on the cameras and recorder).
3. The three health checks: `/ISAPI/ContentMgmt/Storage`, `/ISAPI/System/status`, `/ISAPI/System/time`.
4. Router VPN client screenshots, and on the server `systemd-detect-virt` and `uname -r`.
5. Hikvision licence terms, only if C is ever pursued.
6. The decision to build **A** (receiver, private address per recorder, log screen on the computer layout, then turning real faults into the normal system alerts).

**Cautions**
- Face data is personal biometric data (India's Digital Personal Data Protection Act 2023): consent, purpose and safe handling first; check with a lawyer before offering it. Today the product counts people and reads plates and does not recognise faces.
- Do not commit any Hikvision toolkit files to this repository (licence). Never paste account logins, keys or licence codes into chat.
