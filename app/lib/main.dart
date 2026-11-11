import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';
import 'models.dart';
import 'browser.dart';
import 'firebase_push.dart';
import 'push.dart';
import 'repo.dart';
import 'scope.dart';
import 'screens/more.dart';
import 'widgets/brand.dart';
import 'widgets/glass_nav.dart';
import 'widgets/profile_menu.dart';
import 'screens/signin.dart';
import 'screens/alerts.dart';
import 'screens/cameras.dart';
import 'screens/activity.dart';
import 'screens/home.dart';
import 'screens/set_password.dart';
import 'screens/wizard.dart';
import 'theme.dart';

/// `--dart-define=DEMO=true` runs with demo data. Otherwise the app signs in against the server it was loaded from (or `API_BASE`). The phone apps have no
/// "server it was loaded from", so they are built with `--dart-define=API_BASE=https://your-domain` (see docs/MOBILE.md).
const _demo = bool.fromEnvironment('DEMO');
const _base = String.fromEnvironment('API_BASE');

void main() => runApp(_demo ? IVisionApp(repo: MockRepository()) : IVisionApp(client: ApiClient(base: _base.isEmpty ? null : _base), push: const FirebasePush(), browser: realBrowser()));

/// Screens slide in from the right and can be dragged back from the left edge, on every platform, the way iPhone apps (and WhatsApp on iPhone) do.
const _pageTransitions = PageTransitionsTheme(builders: {
  TargetPlatform.android: CupertinoPageTransitionsBuilder(),
  TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
  TargetPlatform.macOS: CupertinoPageTransitionsBuilder(),
  TargetPlatform.windows: CupertinoPageTransitionsBuilder(),
  TargetPlatform.linux: CupertinoPageTransitionsBuilder(),
  TargetPlatform.fuchsia: CupertinoPageTransitionsBuilder(),
});

class IVisionApp extends StatefulWidget {
  const IVisionApp({super.key, this.repo, this.client, this.push = const NoPush(), this.browser = const Browser()});
  final Repository? repo; // given: no sign-in (demo and tests)
  final ApiClient? client;
  final PushService push;        // where this device's push address comes from (nothing until the Firebase project exists)
  final Browser browser;         // the address the app was opened at, and the hand-offs to the staff console
  @override
  State<IVisionApp> createState() => _IVisionAppState();
}

class _IVisionAppState extends State<IVisionApp> {
  ThemeChoice choice = ThemeChoice.light; // light by default; dark and auto only when chosen
  Repository? repo;
  bool starting = false;
  String? note;
  PushRegistration? _push;
  String? _inviteToken;        // opened from an invitation link: choose a password first
  bool _waitingForConsole = false, _viewEnded = false;
  ViewAsGrant? _viewAs;        // staff looking at this customer's app, view only
  static const _themeKey = 'gi_next_theme';

  @override
  void initState() {
    super.initState();
    repo = widget.repo;
    if (repo != null) _startPush();
    _loadTheme();
    final c = widget.client;
    if (c != null && widget.browser.path == '/set-password') {
      _inviteToken = widget.browser.query['token'] ?? '';
      widget.browser.replaceUrl('/set-password');           // the one-time token does not stay in the address bar
      return;
    }
    if (c != null && widget.browser.openedByStaff) {
      _waitingForConsole = true;
      _receiveView(c);
      return;
    }
    if (repo == null && c != null) {
      starting = true;
      c.restore().then((_) {
        if (!mounted) return;
        setState(() {
          starting = false;
          if (c.signedIn) {
            repo = ApiRepository(c);
            _startPush();
          }
        });
      });
    }
  }

  /// Staff opened this tab from the console: wait for the view-only session, then show the customer's app.
  Future<void> _receiveView(ApiClient c) async {
    final g = await widget.browser.waitForViewAs();
    if (!mounted) return;
    setState(() {
      _waitingForConsole = false;
      if (g == null) return;
      c.useViewAs(g.token);
      _viewAs = g;
      repo = ApiRepository(c);
      widget.browser.replaceUrl('/');
    });
  }

  Future<void> _loadTheme() async {
    try {
      final v = (await SharedPreferences.getInstance()).getString(_themeKey);
      final c = ThemeChoice.values.where((e) => e.name == v).firstOrNull;
      if (c != null && mounted) setState(() => choice = c);
    } catch (_) {/* no storage: stay on light */}
  }

  Future<void> _setTheme(ThemeChoice c) async {
    setState(() => choice = c);
    try {
      await (await SharedPreferences.getInstance()).setString(_themeKey, c.name);
    } catch (_) {}
  }

  void _startPush() {
    final r = repo;
    if (r == null) return;
    _push = PushRegistration(r, widget.push)..start();      // before the screen is built, so Settings can show its button
  }

  void _signedIn() {
    setState(() {
      repo = ApiRepository(widget.client!);
      note = null;
      _startPush();
    });
  }

  Future<void> _signOut([String? message]) async {
    final v = _viewAs;
    if (v != null) {                         // "Stop" on the view-only banner: tell the console, which ends the session
      widget.browser.stopViewAs(v.sessionId);
      if (mounted) setState(() { _viewAs = null; repo = null; _viewEnded = true; });
      return;
    }
    await _push?.stop();
    _push = null;
    await repo?.signOut();
    if (mounted) setState(() { repo = widget.repo; note = message; });
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jas iVision',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light).copyWith(pageTransitionsTheme: _pageTransitions),
        darkTheme: buildTheme(Brightness.dark).copyWith(pageTransitionsTheme: _pageTransitions),
        themeMode: switch (choice) { ThemeChoice.light => ThemeMode.light, ThemeChoice.dark => ThemeMode.dark, ThemeChoice.auto => ThemeMode.system },
        home: _inviteToken != null
            ? SetPasswordPage(client: widget.client!, token: _inviteToken!, onDone: () { widget.browser.replaceUrl('/'); setState(() { _inviteToken = null; note = 'Your password is saved. Sign in with it now.'; }); })
            : _waitingForConsole
                ? const Scaffold(body: Loading(text: "Opening the customer's app"))
                : _viewEnded
                    ? const Scaffold(body: Center(child: Padding(padding: EdgeInsets.all(24), child: Text('The view-only session has ended. You can close this tab.', textAlign: TextAlign.center))))
                    : starting
                        ? const Scaffold(body: Loading(text: 'Starting Jas iVision'))
                        : repo == null
                            ? (_viewAs == null && widget.browser.openedByStaff
                                ? const Scaffold(body: Center(child: Padding(padding: EdgeInsets.all(24), child: Text("Could not open the customer's app. Close this tab and try again from the console.", textAlign: TextAlign.center))))
                                : SignInPage(client: widget.client!, onDone: _signedIn, message: note, browser: widget.browser))
                            : AppScope(onSignedOut: () => _signOut('Please sign in again.'), child: Shell(repo: repo!, choice: choice, onChoice: _setTheme, onSignOut: _signOut, push: _push, viewing: _viewAs?.tenant)),
      );
}

class Shell extends StatefulWidget {
  const Shell({super.key, required this.repo, required this.choice, required this.onChoice, required this.onSignOut, this.push, this.viewing});
  final Repository repo;
  final ThemeChoice choice;
  final ValueChanged<ThemeChoice> onChoice;
  final void Function([String? message]) onSignOut;
  final PushRegistration? push;      // this device's push, for the "Turn on notifications" button
  final String? viewing;             // staff looking at this customer's app: the customer's name, and a banner saying so
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  int openAlerts = 0;
  Me? me;

  static const _defs = [NavIcons.home, NavIcons.cameras, NavIcons.aiWatch, NavIcons.activity, NavIcons.more];
  static const _subtitles = ['How your business is doing right now', 'Every camera, and live view when you need it', 'What iVision watches for, and who hears about it', 'What your cameras have seen', 'Reports and settings'];
  static const _home = 0, _cams = 1, _watch = 2, _activity = 3;
  final _visited = <int>{0};        // a place is built the first time you open it, then keeps where you were
  final _refresh = ValueNotifier<int>(0);

  @override
  void initState() {
    super.initState();
    widget.repo.me().then((m) { if (mounted) setState(() => me = m); }).catchError((_) {});
    _badge();
  }

  void _badge() => widget.repo.health().then((h) { if (mounted) setState(() => openAlerts = h.openAlerts); }).catchError((_) {});

  Widget _page(int i) => switch (i) {
        _home => HomePage(repo: widget.repo, me: me, refresh: _refresh, onOpenAlerts: _openAlerts, onOpenCameras: () => _go(_cams)),
        _cams => CamerasPage(repo: widget.repo, onAddAlert: _addAlertFor),
        _watch => ManageAlertsPage(repo: widget.repo, embedded: true),
        _activity => ActivityPage(repo: widget.repo, refresh: _refresh),
        _ => MorePage(repo: widget.repo, onSignOut: (m) => widget.onSignOut(m), push: widget.push),
      };

  void _go(int i) {
    setState(() { tab = i; _visited.add(i); });
    if (i == _home || i == _activity) _refresh.value++;        // Home and Activity show fresh numbers each time you come back
    _badge();
  }

  /// What happened and what needs you: a screen of its own, from the bell at the top right.
  Future<void> _openAlerts() async {
    await Navigator.of(context).push<void>(MaterialPageRoute(builder: (_) => AlertsScreen(repo: widget.repo, onChanged: _badge)));
    _badge();
    _refresh.value++;
  }

  /// "Add an alert for this camera" opens the new-alert steps with the camera already chosen, then shows AI Watch.
  Future<void> _addAlertFor(CameraItem c) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, preselected: c)));
    if (saved == true && mounted) _go(_watch);
  }

  /// The bell: alerts that need you, with how many.
  Widget _bell() {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      label: openAlerts > 0 ? 'Alerts, $openAlerts need you' : 'Alerts',
      child: InkResponse(
        radius: 26,
        onTap: _openAlerts,
        child: SizedBox(
          width: 44,
          height: 44,
          child: Stack(alignment: Alignment.center, clipBehavior: Clip.none, children: [
            Container(width: 40, height: 40, decoration: BoxDecoration(color: t.surface, shape: BoxShape.circle, border: Border.all(color: t.line)), child: Icon(Icons.notifications_none_rounded, size: 22, color: t.fg)),
            if (openAlerts > 0)
              Positioned(
                right: 0,
                top: 0,
                child: Container(
                  constraints: const BoxConstraints(minWidth: 18),
                  padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                  decoration: BoxDecoration(color: t.crit, borderRadius: BorderRadius.circular(10), border: Border.all(color: t.bg, width: 1.5)),
                  child: Text(openAlerts > 99 ? '99+' : '$openAlerts', textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w700, height: 1.2)),
                ),
              ),
          ]),
        ),
      ),
    );
  }

  Widget _profile() => ProfileButton(me: me, choice: widget.choice, onChoice: widget.onChoice, repo: widget.repo, onSignOut: (m) => widget.onSignOut(m));

  String get _greeting {
    final h = DateTime.now().hour;
    final g = h < 12 ? 'Good morning' : (h < 17 ? 'Good afternoon' : 'Good evening');
    return me == null || me!.first.isEmpty ? g : '$g, ${me!.first}';
  }

  String get _today {
    const wd = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const mo = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    final n = DateTime.now();
    return '${wd[n.weekday - 1]}, ${n.day} ${mo[n.month - 1]}${me == null || me!.business.isEmpty ? '' : ' · ${me!.business}'}';
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final t = Tok.of(context);
    // Switching places is instant and each keeps its scroll position and what it loaded, like WhatsApp and the iPhone's own apps.
    final content = SelectionArea(child: IndexedStack(index: tab, children: [for (var i = 0; i < _defs.length; i++) _visited.contains(i) ? _page(i) : const SizedBox.shrink()]));
    final body = widget.viewing == null ? content : Column(children: [_ViewBanner(name: widget.viewing!, onStop: () => widget.onSignOut()), Expanded(child: content)]);
    final navItems = [for (var i = 0; i < _defs.length; i++) NavDef(_defs[i].icon, _defs[i].selected, _defs[i].label, custom: _defs[i].custom)];
    if (wide) {
      return Scaffold(
        body: Row(children: [
          Container(
            width: 264,
            decoration: BoxDecoration(color: t.surface, border: Border(right: BorderSide(color: t.line))),
            child: SafeArea(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Padding(padding: EdgeInsets.fromLTRB(22, 26, 22, 22), child: Align(alignment: Alignment.centerLeft, child: BrandLogo(style: LogoStyle.compact, height: 52))),
                for (var i = 0; i < _defs.length; i++) _NavItem(def: _defs[i], selected: tab == i, onTap: () => _go(i)),
                const Spacer(),
                Padding(padding: const EdgeInsets.fromLTRB(22, 4, 22, 20), child: Text(tagline, style: TextStyle(color: t.fg2.withValues(alpha: .85), fontSize: 11.5, fontStyle: FontStyle.italic))),
              ]),
            ),
          ),
          Expanded(
            child: Column(children: [
              Container(
                height: 88,
                padding: const EdgeInsets.symmetric(horizontal: 32),
                child: Row(children: [
                  Expanded(child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(tab == 0 ? _greeting : _defs[tab].label, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
                    Text(tab == 0 ? _today : _subtitles[tab], style: TextStyle(color: t.fg2, fontSize: 14)),
                  ])),
                  _bell(),
                  const SizedBox(width: 12),
                  _profile(),
                ]),
              ),
              Expanded(child: Align(alignment: Alignment.topCenter, child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 1120), child: body))),
            ]),
          ),
        ]),
      );
    }
    return Scaffold(
      extendBody: true,
      appBar: AppBar(
        toolbarHeight: 60,
        titleSpacing: 16,
        title: const Align(alignment: Alignment.centerLeft, child: BrandLogo(style: LogoStyle.compact, height: 34)),
        actions: [_bell(), const SizedBox(width: 4), _profile(), const SizedBox(width: 16)],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line)),
      ),
      body: body,
      bottomNavigationBar: AppTabBar(items: navItems, index: tab, onTap: _go),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({required this.def, required this.selected, required this.onTap});
  final NavDef def;
  final bool selected;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2),
      child: Semantics(
        button: true,
        selected: selected,
        label: def.label,
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            height: 48,
            padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(color: selected ? t.brandSoft : Colors.transparent, borderRadius: BorderRadius.circular(14)),
            child: Row(children: [
              AnimatedContainer(duration: const Duration(milliseconds: 180), width: 4, height: selected ? 22 : 0, margin: const EdgeInsets.only(right: 12, left: -6), decoration: BoxDecoration(color: t.accent, borderRadius: BorderRadius.circular(4))),
              def.glyph(selected, selected ? t.brand : t.fg2, 24),
              const SizedBox(width: 14),
              Expanded(child: Text(def.label, style: TextStyle(color: selected ? t.brand : t.fg, fontWeight: selected ? FontWeight.w700 : FontWeight.w600, fontSize: 15))),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Shown while staff look at a customer's app: it says so, and "Stop" ends the session.
class _ViewBanner extends StatelessWidget {
  const _ViewBanner({required this.name, required this.onStop});
  final String name;
  final VoidCallback onStop;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      width: double.infinity,
      color: t.warnSoft,
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
      child: Row(children: [
        Icon(Icons.visibility_outlined, size: 18, color: t.warnInk),
        const SizedBox(width: 10),
        Expanded(child: Text.rich(TextSpan(style: TextStyle(color: t.warnInk, fontSize: 13.5, height: 1.3), children: [
          const TextSpan(text: 'You are looking at '),
          TextSpan(text: name, style: const TextStyle(fontWeight: FontWeight.w700)),
          const TextSpan(text: ' as Jas iVision staff. View only. Everything you open is logged.'),
        ]))),
        TextButton(onPressed: onStop, child: Text('Stop', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w700))),
      ]),
    );
  }
}
