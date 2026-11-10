// The real push service. The web version talks to web/push.js; the phone version uses the Firebase plugins. Chosen by the platform at build time.
export 'firebase_push_io.dart' if (dart.library.js_interop) 'firebase_push_web.dart';
