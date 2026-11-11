// The few things the app needs from the browser itself: the address it was opened at, handing a staff sign-in to the staff console, and staff "view as customer".
// On a phone app none of these apply, and the plain defaults are used.
export 'browser_stub.dart' if (dart.library.js_interop) 'browser_web.dart';
