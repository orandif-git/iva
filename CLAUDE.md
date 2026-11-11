# Jas iVision

- **Design:** the customer app has one approved design system. Read `docs/DESIGN.md` before changing or adding any screen, and follow it: use the shared
  tokens and pieces (`app/lib/theme.dart`, `app/lib/widgets/motion.dart`, `app/lib/screens/common.dart`), never hand-written colours, corner sizes or animations.
- **Words:** plain language for shop owners. No setting names, no jargon.
- **Checks:** `flutter analyze` and `flutter test` in `app/`; `pytest` in `backend/`. Do not run `dart format` on existing files.
- **Deploying the app:** `scripts/build_app.sh` builds it into `backend/web_next`, which is committed.
