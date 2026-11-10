import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import 'push.dart';

/// The Android app's Firebase project values. They identify the project and are public by design (they are also inside google-services.json).
const _android = FirebaseOptions(
  apiKey: 'AIzaSyCAMKHyl2qdZAq141kT42CQsoSFIxaBXEY',
  appId: '1:860950377806:android:28f3f8e1b652b12542da1f',
  messagingSenderId: '860950377806',
  projectId: 'ivision-ade8d',
  storageBucket: 'ivision-ade8d.firebasestorage.app',
);

/// Push on an Android phone. Other platforms (iPhone needs an Apple push key first) say they are not supported, and the app carries on without push.
class FirebasePush implements PushService {
  const FirebasePush();
  static bool _ready = false;

  @override
  bool get supported => !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<void> _init() async {
    if (_ready) return;
    await Firebase.initializeApp(options: _android);
    _ready = true;
  }

  @override
  Future<bool> granted() async {
    try {
      if (!supported) return false;
      await _init();
      final s = (await FirebaseMessaging.instance.getNotificationSettings()).authorizationStatus;
      return s == AuthorizationStatus.authorized || s == AuthorizationStatus.provisional;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<String?> token({bool ask = false}) async {
    try {
      if (!supported) return null;
      await _init();
      final m = FirebaseMessaging.instance;
      var s = (await m.getNotificationSettings()).authorizationStatus;
      if (s == AuthorizationStatus.notDetermined && ask) s = (await m.requestPermission()).authorizationStatus;
      if (s != AuthorizationStatus.authorized && s != AuthorizationStatus.provisional) return null;
      return await m.getToken();
    } catch (_) {
      return null;
    }
  }

  @override
  String get platform => 'android';

  @override
  String get hint => '';
}
