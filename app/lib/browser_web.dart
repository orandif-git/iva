import 'dart:async';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';

import 'package:web/web.dart' as web;

import 'browser_base.dart';
export 'browser_base.dart';

Browser realBrowser() => const WebBrowser();

class WebBrowser extends Browser {
  const WebBrowser();

  web.Window? get _opener {
    final o = web.window.opener;
    return o.isA<web.Window>() ? o as web.Window : null;
  }

  @override
  String get path => Uri.base.path;

  @override
  Map<String, String> get query => Uri.base.queryParameters;

  @override
  void replaceUrl(String path) => web.window.history.replaceState(null, '', path);

  @override
  void handOffToStaff(String refreshToken) {
    web.window.sessionStorage.setItem('gi_staff_rt', refreshToken);        // where the staff console looks for it
    web.window.location.href = '/platform/';
  }

  @override
  bool get openedByStaff => Uri.base.queryParameters['viewas'] == '1' && _opener != null;

  @override
  Future<ViewAsGrant?> waitForViewAs({Duration timeout = const Duration(seconds: 10)}) {
    final done = Completer<ViewAsGrant?>();
    late final JSFunction handler;
    handler = ((web.MessageEvent e) {
      if (e.origin != web.window.location.origin) return;
      final d = e.data;
      if (!d.isA<JSObject>()) return;
      final o = d as JSObject;
      if ((o['type'] as JSString?)?.toDart != 'gi-viewas') return;
      final token = (o['token'] as JSString?)?.toDart, session = (o['session_id'] as JSString?)?.toDart, tenant = (o['tenant'] as JSString?)?.toDart;
      if (token == null || session == null || done.isCompleted) return;
      done.complete(ViewAsGrant(token: token, sessionId: session, tenant: tenant ?? ''));
    }).toJS;
    web.window.addEventListener('message', handler);
    final ready = JSObject()..['type'] = 'gi-viewas-ready'.toJS;
    try {
      _opener?.postMessage(ready, web.window.location.origin.toJS);
    } catch (_) {}
    return done.future.timeout(timeout, onTimeout: () => null).whenComplete(() => web.window.removeEventListener('message', handler));
  }

  @override
  void stopViewAs(String sessionId) {
    try {
      final stop = JSObject()
        ..['type'] = 'gi-viewas-stop'.toJS
        ..['session_id'] = sessionId.toJS;
      _opener?.postMessage(stop, web.window.location.origin.toJS);
    } catch (_) {}
    web.window.close();
  }
}
