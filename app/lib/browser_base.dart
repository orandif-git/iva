/// What staff send to a customer's app tab when they open it from the console: a view-only session.
class ViewAsGrant {
  const ViewAsGrant({required this.token, required this.sessionId, required this.tenant});
  final String token, sessionId, tenant;
}

/// The browser, as the app sees it. Tests give the app a fake one.
class Browser {
  const Browser();

  /// The address path the app was opened at, for example "/set-password".
  String get path => '/';
  Map<String, String> get query => const {};

  /// Changes the address in the bar without reloading, so a one-time link is not left there.
  void replaceUrl(String path) {}

  /// A staff member signed in on the customer page: pass the sign-in to the staff console and go there.
  void handOffToStaff(String refreshToken) => throw UnsupportedError('The staff console is opened in a browser.');

  /// Was this tab opened by the staff console to look at a customer's app?
  bool get openedByStaff => false;

  /// Waits for the console to hand over the view-only session. Null if it does not arrive.
  Future<ViewAsGrant?> waitForViewAs({Duration timeout = const Duration(seconds: 10)}) async => null;

  /// Tells the console the view is over, so it can end the session, and closes this tab.
  void stopViewAs(String sessionId) {}
}
