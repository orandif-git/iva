import 'package:flutter/widgets.dart';

/// Lets any screen send the person back to sign-in when the server says the session is over.
class AppScope extends InheritedWidget {
  const AppScope({super.key, required this.onSignedOut, required super.child});
  final VoidCallback onSignedOut;
  static AppScope? maybeOf(BuildContext c) => c.dependOnInheritedWidgetOfExactType<AppScope>();
  @override
  bool updateShouldNotify(AppScope old) => false;
}
