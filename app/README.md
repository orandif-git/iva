# Jas iVision customer app (Flutter)

One codebase for the web app, iOS and Android. This is the test build and the first slice of the new customer app (see the "Customer App Redesign" prototype).

## What is here
- **Home**: health banner, what needs you (Done / Snooze), numbers, system health.
- **Alerts**: inbox (open / all), My alerts, and the five-step "New alert" flow (kind, alert, cameras, settings, who hears about it, save).
- **Area editor** (`lib/widgets/area_editor.dart`): draw any shape on the camera picture (tap corners, tap the first corner to close, drag a corner, Undo, Redraw). Same rules as the server (`lib/geometry/polygon.dart`).
- **Cameras**: list, then a camera screen with **Live view** (2 min, up to 10) and **AI view** (30 s, up to 2 min), Keep watching, Stop, and on a phone **Fit / Fill / Enlarge** (`lib/live/`).
- Text can be selected and copied on every screen (`SelectionArea`); the drawing area and the camera picture opt out so dragging never selects text.
- Light by default; dark is a choice in Settings.

## Run
```bash
flutter run -d chrome                                    # demo data
flutter run -d chrome --dart-define=API_BASE=https://HOST --dart-define=API_TOKEN=...   # the real server
flutter test
flutter build web --release --no-web-resources-cdn       # self-hosted engine, so the strict content-security policy can stay
```
The screens talk to a `Repository` (`lib/repo.dart`). `MockRepository` is the demo; `ApiRepository` calls the endpoints in `API.md` section 4d.

## Test-build results (the gate)
| Check | Result |
| --- | --- |
| Unit and widget tests | 22 passing (shape rules, live clocks, area editor, flows, phone layout) |
| Area drawing in Chromium | tap corners, close with the first corner, drag a corner: works with a mouse; touch uses the same gestures |
| AI view and Enlarge on a 390 px phone | works: countdown, boxes and area drawn, picture turned 90 degrees without cropping |
| Web size, CanvasKit build | app 0.76 MB + engine 2.1 MB (Chrome) or 2.9 MB (others), compressed; about 2.8 to 3.7 MB on the first visit, cached after |
| Web size, WebAssembly build | app 0.82 MB + engine 1.5 MB, compressed: about 2.4 MB; needs a recent browser, falls back to the CanvasKit build |
| First paint, local, no throttling | about 3.5 s |

Not yet proven: video from a real recorder, a mid-range Android phone, an iPhone, the push notification layer, and screen-reader behaviour.
