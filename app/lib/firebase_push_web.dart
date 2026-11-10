import 'dart:js_interop';
import 'dart:js_interop_unsafe';

import 'push.dart';

/// The browser's push, through web/push.js (Firebase's own scripts, kept on our server).
class FirebasePush implements PushService {
  const FirebasePush();

  JSObject? get _gi {
    final v = globalContext['giPush'];
    return v.isA<JSObject>() ? v as JSObject : null;
  }

  @override
  bool get supported {
    try {
      final gi = _gi;
      return gi != null && (gi.callMethod<JSBoolean>('supported'.toJS)).toDart;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<bool> granted() async {
    try {
      return supported && (_gi!.callMethod<JSString>('permission'.toJS)).toDart == 'granted';
    } catch (_) {
      return false;
    }
  }

  @override
  Future<String?> token({bool ask = false}) async {
    try {
      if (!supported) return null;
      final r = await (_gi!.callMethod<JSPromise<JSAny?>>('token'.toJS, ask.toJS)).toDart;
      return r.isA<JSString>() ? (r as JSString).toDart : null;
    } catch (_) {
      return null;
    }
  }

  @override
  String get platform => 'web';

  @override
  String get hint {
    try {
      final gi = _gi;
      return gi == null ? '' : (gi.callMethod<JSString>('hint'.toJS)).toDart;
    } catch (_) {
      return '';
    }
  }
}
