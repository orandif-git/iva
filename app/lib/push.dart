import 'repo.dart';

/// Where the app's push address comes from. Today nothing supplies one: it is filled in when the Firebase project exists (see docs/PUSH.md), by a class
/// that asks Firebase Messaging for the device's token. Everything else (telling the server, forgetting it on sign-out) is already in place.
abstract class PushService {
  /// The device's address, or null when push is not available here or the person said no.
  Future<String?> token();
  String get platform;
}

class NoPush implements PushService {
  const NoPush();
  @override
  Future<String?> token() async => null;
  @override
  String get platform => 'web';
}

/// After sign-in: if the server can send push and this device has an address, give it to the server. Never throws; push is an extra.
class PushRegistration {
  PushRegistration(this.repo, this.service);
  final Repository repo;
  final PushService service;
  String? _registered;

  Future<void> start() async {
    try {
      if (!await repo.pushEnabled()) return;
      final t = await service.token();
      if (t == null || t.isEmpty) return;
      await repo.registerDevice(t, service.platform);
      _registered = t;
    } catch (_) {/* WhatsApp still works */}
  }

  /// On sign-out, so the next person on this device does not get this account's alerts.
  Future<void> stop() async {
    final t = _registered;
    if (t == null) return;
    _registered = null;
    try {
      await repo.removeDevice(t);
    } catch (_) {}
  }
}
