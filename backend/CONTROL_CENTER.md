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

## Shared WhatsApp connection for alerts

Setup, WhatsApp connection has two tabs (Owner only): **Summaries** (the direct-send address and key) and **Alerts** (one address and key for every alert). An alert that "uses the shared connection" sends to the shared address with `Authorization: Bearer <shared key>`, so a changed key is saved once. On the Alerts tab: Save (address, key, or "Use the same key as the summaries"), "Use for all alerts" (switches every alert over; their own address and key are kept but no longer used), and "Send a test" (pick an alert and a number). In an alert's own window, "Use its own address and key" and "Use the shared connection" switch that one alert. "Set up all alerts" needs no address once the shared one is set.

- Endpoints: `GET/PUT /admin/v1/webhooks/connection`, `POST /admin/v1/webhooks/connection/apply`, `POST /admin/v1/webhooks/connection/test` (all Owner only), `use_shared` on `PUT /admin/v1/webhooks/{id}`. Migration 029 (`platform.alert_connection`, `webhook_endpoints.use_shared`).
- Sending resolves the address and headers in `messaging/delivery.py` (`resolve`), also for the platform-fault alert. The key is stored encrypted and only its last four characters are ever returned.
- The earlier per-alert "Access key" box (`POST /admin/v1/webhooks/key`) stays for an alert that uses its own address.

## Test messages

- **Send a test** in an alert's window (Setup, WhatsApp templates) and on the Alerts tab always asks for a mobile number (`to_phone` is required on `POST /admin/v1/webhooks/{id}/send-now`), so a test can never go to a placeholder number. It shows the provider's answer in plain words.
- A test message (from there, or the Test button on a customer's page) is complete: sample values for everything the message uses (people, limit, place, time) and our own sample picture and video, `web/img/sample-alert.jpg` and `sample-alert.mp4` (public, 17 KB and 13 KB), so a video alert arrives as a finished message.
- In any message sent to the provider, a text value with nothing to show is sent as a dash, because WhatsApp refuses an empty text parameter. Link values (`image_url`, `clip_url`, `document_url`) are never replaced; a real alert waits for its clip before it is sent.

## Oldest recording probe (Dahua)

`python -m dahua.probe --oldest IP PORT USER PASSWORD [CHANNELS]` (read-only) asks one recorder for its recordings over very wide periods (since 2000, last 365, 90 and 30 days) on its first channels and prints exactly what the kit returns: the shape of the answer, how many recordings, and the first and last one with their start and end times. It shows whether the exact start of the oldest recording can be read, and how wide a period the recorder accepts, before the gateway and the customer Health page are built to show "Recordings from <date>".

## Live view

Recorder and cameras lists every camera with a **Watch live** button (Owner and Admin). It opens the camera's window and shows moving pictures (about 6 a second, up to 960 pixels wide), with a red "Live · 4:58 left" label. A live view stops by itself after 5 minutes (`GI_LIVE_STREAM_SECONDS`) and can be started again; closing the window stops it at once. Everyone else sees no button. If the video cannot start (the recorder is offline, too many cameras are being watched, no video source) the window shows the still picture and says why. The windows for marking an area, a line or a plate area also have a **Watch live** button in the picture's header, so you can draw over the moving picture; clicking the picture to add a corner keeps the live view running.

- Endpoint: `GET /admin/v1/tenants/{id}/shop/cameras/{camera}/live` (a stream of JPEGs sent as `application/octet-stream`, because iPhone browsers cannot read a `multipart/x-mixed-replace` stream with fetch; `services/live_stream.py`, FFmpeg). At most `GI_LIVE_STREAM_MAX` (8) run at once on the server, then the answer is 429 "try again in a moment". Every start is written to the activity log (`camera.live_view`).
- The console reads the stream with a normal signed-in request (`Api.stream` in `lib.js`), so no token is ever put in an address. If you put a proxy in front, turn response buffering off for this path (the API already sends `X-Accel-Buffering: no`).

## Staying on the same page after a refresh

The address carries where you are (`#customer/<id>/<section>`, `#setup/<tab>`, `#overview`, `#customers`, `#messages`), so refreshing the browser, or opening the same address again, comes back to that page. The step-by-step screens (new customer, new event) are not remembered.

## Overview numbers

Every number on the Overview opens the matching page: Customers, Active and Need a look open the customer list with that status; Cameras and Recorders open the customer list; Sent in 24 hours, Held back and Failed to send open Messages for the last 24 hours with that result. The message log has a "Last 24 hours" range (`hours=24` on `GET /admin/v1/messaging/log`, the last N hours up to now), which is exactly what the Overview counts.

## Health of one customer, and renaming a camera

A customer's left menu has **Health**, after Business hours (every console user). It reads `GET /admin/v1/tenants/{id}/shop/health` (`services/shop_health.py`) and shows, in the same look as System health: a verdict with what needs a look, recorders online and cameras working, messages sent and failed in the last 24 hours, a **Recorders** table (status, last heard, disks and how full, clock, and **Recordings from** the exact start of the oldest recording the recorder holds, with the days), a **Cameras** table (working, last picture, recording now, picture fine / moved / covered / blurry / dark) and the problems of each of the last 7 days (test alerts are not counted). It refreshes with the other pages.

A camera is working when its recorder is online and the analysis worker sent a result in the last 3 minutes.

**Rename** is on every camera in the Cameras list under Recorder and cameras, and on each "Already added" camera in "Cameras found on <recorder>" (Owner, Admin and Onboarding).

The **Recorders** list is compact (status, last heard, a storage summary, and the date its recordings start); click a recorder and a window opens from the right with everything about it: connection (status, address it connects from, firmware, running time, restarts today, dropped connections), storage (total and how full, then each disk with make, serial number, size, free space, state and the share of its life left), recordings (exact start, days held, each channel online and recording), clock, hardware (temperature, fan, camera power) and the connection history of the last 24 hours (a drop followed by a return is one line). A value the recorder did not report reads "Not reported". The data is in `recorders[].detail` of the health response.

## Status lights, the server strip and grouped events

- **Top bar lights:** three lights on the right of the top bar on every page, **CPU**, **Worker** and **Analysis** (cameras analysed of cameras expected). Green is fine, amber needs a look, red needs action. Click one for its numbers; "Open System health" goes to Setup, System health (Owner and Admin). The numbers refresh every 30 seconds. They come from `GET /admin/v1/system/status`, which every console user may read (`services/system_health.py`, `status`).
- **Overview:** a **Server and analysis** panel under the customer numbers: Processor, Memory, Disk (with days left), Analysis worker, Cameras analysed and Messages waiting, with the same limits as System health.
- **Events** of a customer are in two foldable groups on one page: **Smart events** (counting, plates, marked areas and the alerts built on them; you add and delete these) and **System events** (the default checks; switch off to stop). The headers show how many are on. Searching opens every group that has a match.
- The name in the middle of the top bar is "JAS" in the brand colour, then "iVision" in italics and "Control Center" in the normal text colour.

## On a phone
On screens up to 760 px wide the Control Center switches to a phone layout (`web/console/phone.css`, loaded only on small screens; the same page and scripts, so nothing is built twice).
- One column; the page itself scrolls. A header sits under the phone's status bar (`env(safe-area-inset-top)`), and a floating glass tab bar sits above the home bar: **Overview, Customers, Messages, Health, More**. Health is left out for roles that cannot see System health. A capsule slides to the tapped tab and the page slides in from the side you move towards (both switch off for people who asked their phone for less motion).
- A customer opens with chips along the top (Health first). Events are cards with a switch; tapping one opens a bottom sheet with the name, who gets it, the settings and Save/Cancel. Dialogs and side panels rise from the bottom as sheets.
- Phone: Overview, Customers (health, events on/off and edit, cameras and rename, people, hours, messages), Messages, System health, change password, sign out, live view (stops after 2 minutes on a phone, 5 on a computer).
- Computer only (hidden on a phone, with "best done on a larger screen" notes, and a collapsed "On a larger screen" group in More): adding customers or events, deleting events, drawing or redrawing areas, WhatsApp templates and connection, console users, data kept, activity log, editing a customer, the end date, deleting a customer, adding recorders.
- Next steps (not built yet): install to the home screen (service worker scoped to `/console/`), push notifications by role, and an optional "Stay signed in on this phone" (30 days, off by default).

### Compact lists and People count
- On a phone, a customer's Cameras (recorders and cameras), People and Hours are compact grouped lists (one rounded block, thin lines, one line of detail per row). Tapping a row opens a sheet: a recorder shows its status and connection details; a camera has Rename and Watch live; a person has name, mobile, the Alerts and Summary ticks, Save and Remove; a day has the open switch, opening and closing times, copy to every open day, and Save. Health lists its recorders and cameras the same way.
- The profile picture is not in the phone header any more; the profile, Change password, light/dark and Sign out are under More.
- People count (computer and phone): one block of numbers (people in, people out, busiest hour or day, about inside now) and the graph with the busiest hour shaded and tagged "Busiest".

## The app on a phone's home screen, staying signed in, and notifications
- **Add to home screen.** `web/console/manifest.webmanifest` (name, icons, opens at `/console/`, standalone) and a service worker, `web/console/sw.js`, with scope `/console/` (the old `/sw.js` is a different file that only retires itself). The worker keeps the app's own pages and pictures so it opens quickly and shows something offline; it tries the network first, so a new version is used as soon as it is deployed, and it never keeps anything from `/admin/`. More shows "Add to home screen": a ready-made prompt on Android and Chrome, and the Share, Add to Home Screen steps on an iPhone (Safari). The row disappears once the app is installed.
- **Stay signed in on this phone.** Off by default. A sign-in lasts 12 hours and ends when the app closes. With the switch on (More, Stay signed in on this phone) it lasts 30 days from the last use (`GI_STAFF_REMEMBER_DAYS`), each use starts the 30 days again, and the app opens straight in after it was closed. The server keeps the length with the sign-in (`POST /admin/v1/auth/remember` changes it), a refresh token still works once, and Sign out or "Sign out everywhere else" ends it. The token is kept in the phone's own storage only while the switch is on.
- **Notifications by role.** Web Push with no Firebase: More, Notifications turns them on for this phone and each person chooses what to hear about (their own switches win over their role's start). Roles start with: Owner and Admin everything; Support customer problems; Onboarding ending soon. Kinds: Server problems (worker, disk, processor, cameras silent, messages waiting), Customer problems (recorder offline or back), Ending soon (once a day from 9:00, customers ending within 7 days), Messages not sent (at most one note every half hour), Daily summary (once a day from 8:30). A watcher (`services/staff_push.py`, every minute) announces a problem once, and the all-clear once. Needs `GI_VAPID_PUBLIC_KEY` and `GI_VAPID_PRIVATE_KEY` (see DEPLOY.md). On an iPhone it works only from the home-screen app (iOS 16.4 or newer).
- **Face ID or fingerprint (passkeys).** More, Face ID sets it up on a phone (the phone asks for Face ID and keeps the private key; the server keeps only the public half, `platform.passkeys`). The sign-in page then shows "Sign in with Face ID or fingerprint" (only on a phone or computer that has one) and signs in with the same tokens as a password; the password keeps working. Rules: Face ID itself is required, not just a tap; each challenge works once and for 5 minutes; the answer must come from this site's own address (`GI_PUBLIC_BASE_URL`), so a look-alike site gets nothing; ES256 keys only; a key whose counter goes backwards is refused as a copy (phones that keep no counter, such as iCloud keys, are fine). Apple may keep the key in iCloud, so it can also work on the person's other Apple devices. A person removes their own phones in the same place; Owner and Admin can remove all of a person's Face ID sign-ins (Console users, **Remove Face ID**) after a lost phone, and setting a new password from a reset link removes them too. Set-up and removal are in the activity log.

## Who gets an alert nobody chose people for
An event's **Goes to** list used to start empty, so an alert such as "Recorder offline" was raised but sent to nobody and left no trace in Messages. Now, until someone chooses (saves the event with a list, even an empty one), the alert goes to **everyone of the customer who has a mobile number** (`alert_rules.recipients_set` says whether someone chose; migration 032 keeps every list that already had people exactly as it was). The event's sheet shows those people ticked, so the screen shows what really happens, and saving it makes the choice explicit. Daily and weekly summaries are never sent this way. An event that is on but goes to nobody shows a **Goes to nobody** tag in the Events list. A customer with nobody added still just records the alert.

## Hikvision alerts (a recorder that stays on Hik-Connect)
A Hikvision recorder is not connected like a Dahua one. Instead it can send its **fault alerts** to a private address on our server. This gives disk full, disk error, camera lost and recording problem alerts; it does not give the full health page, and it keeps Hik-Connect working.
- **Where:** a customer's *Recorder and cameras* page, under the recorders (computer layout only). *Add Hikvision alerts*, give the recorder a name, and the window shows the **command** and **address** once. Only a one-way fingerprint of the secret code in the address is kept. *New code* replaces it if it ever leaks (the old address stops at once); *Remove* deletes the link and its log.
- **Setting up a shop (once per recorder):** on a computer in the shop, paste the command (put the recorder's address in place of RECORDER_ADDRESS; it asks for the recorder's password). Then on the recorder's web page, *Configuration, Event*: tick **Notify Surveillance Center** for Video Loss and for the faults under Exception. Open the address in a browser from the shop's network: it says "Ready to receive alerts".
- **What happens to a message:** disk full becomes *Recorder storage low*, disk error *Recorder disk failure*, recording problem *Recording stopped*, a lost camera *Camera offline*. They go to the same people as any other alert. Everything else (wrong sign-in, motion and so on) is only written to the log. The recorder's "all is well" messages (every few seconds) are written once in a while, not each time. The log (the *Messages* button) is kept 30 days and shows what we did with each message.
- **Safety of this public address:** it reaches the database through a narrow role (`public_edge`) that can read no customer table, only call a few fixed lookups. The alert itself is raised under the customer's own restricted role. Wrong codes from one address are slowed down (30 a minute), and one link can write at most 600 log lines an hour.
- Nothing is raised for a customer whose service has stopped. Who may add, replace or remove: Owner, Admin and Onboarding; everyone on the team can look.
- **Plain HTTP only:** a Hikvision recorder (checked on a DS-7632NXI-K2) refuses HTTPS for event alerts, so its address is `http://<our site>/hik/events/<code>` on port 80. Our web server must answer plain HTTP for that path without redirecting it to HTTPS (it does today; a recorder does not follow redirects). Anyone watching the shop's internet line could read those messages, which carry fault details only, never passwords or video.
- **Not verified yet:** a real "camera lost" message from a recorder with cameras. The log is there so a new kind of message shows up the first time it arrives.
