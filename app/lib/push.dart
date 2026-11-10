import 'package:flutter/foundation.dart';

import 'repo.dart';

/// Where the app's push address comes from: Firebase, on the web (see web/push.js) and on Android. iPhone comes later, with the Apple account.
abstract class PushService {
  /// Can this device receive push at all (the browser or phone supports it and the app is set up for it)?
  bool get supported;

  /// Has the person already allowed notifications on this device? Never shows a permission box.
  Future<bool> granted();

  /// The device's address, or null. With [ask] the permission box may open, so only call that from a tap.
  Future<String?> token({bool ask = false});

  String get platform;

  /// When push is not available here: what the person can do about it ("ios-install": add the app to the Home Screen), or empty.
  String get hint => '';
}

class NoPush implements PushService {
  const NoPush();
  @override
  bool get supported => false;
  @override
  Future<bool> granted() async => false;
  @override
  Future<String?> token({bool ask = false}) async => null;
  @override
  String get platform => 'web';
  @override
  String get hint => '';
}

/// Gives this device's address to the server, and takes it back on sign-out. Never throws: push is an extra, WhatsApp still works.
class PushRegistration {
  PushRegistration(this.repo, this.service);
  final Repository repo;
  final PushService service;
  String? _registered;

  /// Is this device registered for push right now? Settings listens to this.
  final on = ValueNotifier<bool>(false);

  /// After sign-in: if the person already allowed notifications on this device, register quietly. Never opens a permission box.
  Future<void> start() async {
    try {
      if (!service.supported || !await service.granted() || !await repo.pushEnabled()) return;
      await _register(await service.token());
    } catch (_) {}
  }

  /// From the "Turn on notifications" button: may open the permission box. Returns a short sentence for the person.
  Future<String> enable() async {
    try {
      if (!service.supported) return 'This device cannot show notifications from the app.';
      if (!await repo.pushEnabled()) return 'Notifications are not switched on for your account yet. Ask Jas iVision support.';
      final t = await service.token(ask: true);
      if (t == null || t.isEmpty) return 'Notifications are blocked on this device. Allow them in the browser or phone settings, then try again.';
      await _register(t);
      return 'Notifications are on for this device.';
    } catch (_) {
      return 'We could not turn notifications on. Please try again.';
    }
  }

  Future<void> _register(String? t) async {
    if (t == null || t.isEmpty) return;
    await repo.registerDevice(t, service.platform);
    _registered = t;
    on.value = true;
  }

  /// Stop pushing to this device (the person turned it off, or signed out), so the next person on it does not get this account's alerts.
  Future<void> stop() async {
    final t = _registered;
    _registered = null;
    on.value = false;
    if (t == null) return;
    try {
      await repo.removeDevice(t);
    } catch (_) {}
  }
}
