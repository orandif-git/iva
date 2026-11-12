# Jas iVision

- **Design:** the customer app and the staff console share one approved design system. Read `docs/DESIGN.md` before changing or adding any screen, and follow it: use the shared
  tokens and pieces (`app/lib/theme.dart`, `app/lib/widgets/motion.dart`, `app/lib/screens/common.dart`), never hand-written colours, corner sizes or animations. The staff console takes the same tokens from the design-system block at the end of `backend/web/css/app.css`.
- **Words:** plain language for shop owners. No setting names, no jargon.
- **Checks:** `flutter analyze` and `flutter test` in `app/`; `pytest` in `backend/`. Do not run `dart format` on existing files.
- **Design guard:** run `dart run tool/check_design.dart` in `app/` (also part of `flutter test` in `test/design_test.dart`). It fails on a hand-written hex colour or a card-sized corner outside the shared tokens.
- **Live video:** smooth WebRTC needs go2rtc and `GI_GO2RTC_URL`; without it the app falls back to the picture stream. See `API.md` (§4d) for candidates, the H.264 transcoding and the `smooth_video_unavailable` fallback.
- **Deploying the app:** `scripts/build_app.sh` builds it into `backend/web_next`, which is committed.
