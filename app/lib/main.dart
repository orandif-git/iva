import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';
import 'models.dart';
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
import 'screens/home.dart';
import 'theme.dart';

/// `--dart-define=DEMO=true` runs with demo data. Otherwise the app signs in against the server it was loaded from (or `API_BASE`).
const _demo = bool.fromEnvironment('DEMO');
const _base = String.fromEnvironment('API_BASE');

void main() => runApp(_demo ? IVisionApp(repo: MockRepository()) : IVisionApp(client: ApiClient(base: _base.isEmpty ? null : _base)));

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
  const IVisionApp({super.key, this.repo, this.client, this.push = const NoPush()});
  final Repository? repo; // given: no sign-in (demo and tests)
  final ApiClient? client;
  final PushService push;        // where this device's push address comes from (nothing until the Firebase project exists)
  @override
  State<IVisionApp> createState() => _IVisionAppState();
}

class _IVisionAppState extends State<IVisionApp> {
  ThemeChoice choice = ThemeChoice.light; // light by default; dark and auto only when chosen
  Repository? repo;
  bool starting = false;
  String? note;
  PushRegistration? _push;
  static const _themeKey = 'gi_next_theme';

  @override
  void initState() {
    super.initState();
    repo = widget.repo;
    if (repo != null) _startPush();
    _loadTheme();
    final c = widget.client;
    if (repo == null && c != null) {
      starting = true;
      c.restore().then((_) {
        if (!mounted) return;
        setState(() { starting = false; if (c.signedIn) repo = ApiRepository(c); });
        if (c.signedIn) _startPush();
      });
    }
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
    _push = PushRegistration(r, widget.push)..start();
  }

  void _signedIn() {
    setState(() { repo = ApiRepository(widget.client!); note = null; });
    _startPush();
  }

  Future<void> _signOut([String? message]) async {
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
        home: starting
            ? const Scaffold(body: Loading(text: 'Starting Jas iVision'))
            : repo == null
                ? SignInPage(client: widget.client!, onDone: _signedIn, message: note)
                : AppScope(onSignedOut: () => _signOut('Please sign in again.'), child: Shell(repo: repo!, choice: choice, onChoice: _setTheme, onSignOut: _signOut)),
      );
}

class Shell extends StatefulWidget {
  const Shell({super.key, required this.repo, required this.choice, required this.onChoice, required this.onSignOut});
  final Repository repo;
  final ThemeChoice choice;
  final ValueChanged<ThemeChoice> onChoice;
  final void Function([String? message]) onSignOut;
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  int openAlerts = 0;
  Me? me;
  int wizardKey = 0;
  CameraItem? alertFor;

  static const _defs = [NavIcons.home, NavIcons.cameras, NavIcons.aiWatch, NavIcons.alerts, NavIcons.more];
  static const _subtitles = ['How your business is doing right now', 'Every camera, and live view when you need it', 'What iVision watches for, and who hears about it', 'What happened, and what needs you', 'Reports and settings'];
  static const _home = 0, _cams = 1, _watch = 2, _alerts = 3;
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
        _home => HomePage(repo: widget.repo, me: me, refresh: _refresh, onOpenAlerts: () => _go(_alerts), onOpenCameras: () => _go(_cams)),
        _cams => CamerasPage(repo: widget.repo, onAddAlert: (c) => setState(() { alertFor = c; wizardKey++; _visited.add(_alerts); tab = _alerts; })),
        _watch => ManageAlertsPage(repo: widget.repo, embedded: true),
        _alerts => AlertsPage(key: ValueKey('alerts$wizardKey'), repo: widget.repo, refresh: _refresh, startWizardFor: alertFor, onChanged: _badge),
        _ => MorePage(repo: widget.repo, onSignOut: (m) => widget.onSignOut(m)),
      };

  void _go(int i) {
    setState(() { tab = i; _visited.add(i); if (i != _alerts) alertFor = null; });
    if (i == _home || i == _alerts) _refresh.value++;        // Home and Alerts show fresh numbers each time you come back
    _badge();
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
    final body = SelectionArea(child: IndexedStack(index: tab, children: [for (var i = 0; i < _defs.length; i++) _visited.contains(i) ? _page(i) : const SizedBox.shrink()]));
    final navItems = [for (var i = 0; i < _defs.length; i++) NavDef(_defs[i].icon, _defs[i].selected, _defs[i].label, badge: i == _alerts ? openAlerts : 0, custom: _defs[i].custom)];
    if (wide) {
      return Scaffold(
        body: Row(children: [
          Container(
            width: 264,
            decoration: BoxDecoration(color: t.surface, border: Border(right: BorderSide(color: t.line))),
            child: SafeArea(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Padding(padding: EdgeInsets.fromLTRB(22, 26, 22, 22), child: Align(alignment: Alignment.centerLeft, child: BrandLogo(style: LogoStyle.compact, height: 52))),
                for (var i = 0; i < _defs.length; i++) _NavItem(def: _defs[i], badge: i == _alerts ? openAlerts : 0, selected: tab == i, onTap: () => _go(i)),
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
        actions: [_profile(), const SizedBox(width: 16)],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line)),
      ),
      body: body,
      bottomNavigationBar: AppTabBar(items: navItems, index: tab, onTap: _go),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({required this.def, required this.selected, required this.onTap, this.badge = 0});
  final NavDef def;
  final bool selected;
  final int badge;
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
              if (badge > 0) Container(padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1), decoration: BoxDecoration(color: t.crit, borderRadius: BorderRadius.circular(10)), child: Text(badge > 99 ? '99+' : '$badge', style: const TextStyle(color: Colors.white, fontSize: 11.5, fontWeight: FontWeight.w700))),
            ]),
          ),
        ),
      ),
    );
  }
}
