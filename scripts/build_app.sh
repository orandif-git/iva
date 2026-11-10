#!/usr/bin/env bash
# Builds the new customer app (Flutter, in /app) and puts it where the API serves it: backend/web_next, at /next/.
# Needs the Flutter SDK. The server itself needs nothing but the built files, so this runs on a developer machine, not on the server.
set -euo pipefail
cd "$(dirname "$0")/../app"
flutter build web --release --no-web-resources-cdn --base-href /next/
rm -rf ../backend/web_next && cp -r build/web ../backend/web_next
cd ../backend/web_next
# only the CanvasKit engine is used; drop the debug symbols and the engines this build never loads
find . -name '*.symbols' -delete
rm -rf canvaskit/skwasm* canvaskit/wimp* canvaskit/webparagraph canvaskit/chromium/skwasm* 2>/dev/null || true
du -sh .
