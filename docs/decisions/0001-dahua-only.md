# 0001: The product connects Dahua recorders only

Date: 10 November 2026. Status: accepted.

## Decision

Jas iVision supports **Dahua NVRs and DVRs only**, connected through Dahua Auto Register. Hikvision recorders are **not supported**: they stay on Hik-Connect, and we do not ask Hikvision customers to move them.

Two other connection types still exist, but only staff can use them:

- **GB28181**, as a fallback for a Dahua model that has no Auto Register page.
- The **"other" brand**, for a recorder that is not Dahua but can speak GB28181.

Customers cannot add either. The product is built and extended on this base.

## Why

The decision came from a hands-on trial of the one route Hikvision offers for direct connections: Hikvision's **Device Gateway** (ISUP 5.0), installed on our own server so that recorders connect straight to us with no Hikvision cloud in between, the same way Dahua recorders connect.

**What worked**
- A real recorder (DS-7616NXI-K1, firmware V4.73.106) registered with our gateway, passed the key check and showed as online.
- Our own client (`backend/isup/client.py` on the parked branch) could look it up, read its status and ask for a stream address.
- Plain commands through the gateway (such as reading the recorder's model and its channel list) worked.

**What was not proven**
- **Live video.** The gateway gave out a stream address and accepted the login, but the recorder refused to start the stream with an error code (3701 from Hikvision's ISUP library) that no public document explains. The test camera on that recorder was offline at the time (`netUnreachable`), so the result is inconclusive, not a clear failure. Hikvision has not yet been asked.
- Hikvision's gateway datasheet lists only one NVR model as tested (DS-7608NI-I2, firmware V4.40, 2020). Ours is not on the list.

**What decided it:** on the recorder tested, the **Platform Access** setting is a single choice, ISUP **or** Hik-Connect. Pointing a recorder at our server switches off the Hik-Connect mobile app for that recorder. We will not take the Hik-Connect app away from customers.

## Alternatives that were considered

| Option | Result |
|---|---|
| Hikvision cloud API (keeps Hik-Connect) | Video would flow through Hikvision's servers, against our rule that video stays on our servers. Needs a Hikvision partner licence, with approval, fees and AI-use terms we do not know. |
| VPN tunnel from each site plus RTSP | Works and keeps Hik-Connect, but needs a router or small box at every site. Not ruled out for the future. |
| Port forwarding plus RTSP | Needs a public IP address at the site (often not available on Indian broadband) and exposes the recorder to the internet. Not offered. |
| Cameras registering over ISUP themselves | Only works for cameras on a network with internet access, not for cameras on the recorder's own PoE ports. Not tested. |

## What changed in the code

- Customers can add Dahua recorders only. Any other brand or GB28181 is refused (403 `staff_only`).
- Hikvision, ISUP and `cloud_api` are refused for everyone (422 `not_supported`).
- Staff add GB28181 and other-brand recorders with `POST /admin/v1/tenants/{id}/recorders` (owner role, a reason is required, audited).
- The database enums keep their `hikvision` and `isup` values, so old rows stay readable. An old row's connection screen answers 409 instead of failing.
- The Hikvision code (gateway client, workflow and a probe script) was not merged. It was kept on the `hikvision-isup` branch, which has been deleted from GitHub. A backup bundle of it is held outside this repository by the project owner.

## When to reopen this

Revisit only if one of these happens:

1. Hikvision explains error 3701 or names a recorder and firmware that streams live video through the Device Gateway **and** lets a recorder keep Hik-Connect at the same time.
2. Enough customers need Hikvision support to justify per-site hardware (the VPN route) or the licensed cloud route.
3. Hikvision confirms in writing that its cloud API may be used to feed our AI analysis.

Open questions for Hikvision: what error 3701 means for `NET_ECMS_StartPushRealStream`; whether DS-7616NXI-K1 on V4.73 is supported for live view over ISUP 5.0; whether a firmware allows ISUP and Hik-Connect together.
