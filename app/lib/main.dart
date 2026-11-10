import 'package:flutter/material.dart';

import 'api_client.dart';
import 'models.dart';
import 'repo.dart';
import 'scope.dart';
import 'screens/more.dart';
import 'widgets/brand.dart';
import 'screens/signin.dart';
import 'screens/alerts.dart';
import 'screens/cameras.dart';
import 'screens/home.dart';
import 'theme.dart';

/// `--dart-define=DEMO=true` runs with demo data. Otherwise the app signs in against the server it was loaded from (or `API_BASE`).
const _demo = bool.fromEnvironment('DEMO');
const _base = String.fromEnvironment('API_BASE');

void main() => runApp(_demo ? IVisionApp(repo: MockRepository()) : IVisionApp(client: ApiClient(base: _base.isEmpty ? null : _base)));

class IVisionApp extends StatefulWidget {
  const IVisionApp({super.key, this.repo, this.client});
  final Repository? repo; // given: no sign-in (demo and tests)
  final ApiClient? client;
  @override
  State<IVisionApp> createState() => _IVisionAppState();
}

class _IVisionAppState extends State<IVisionApp> {
  ThemeMode mode = ThemeMode.light; // light by default, dark only when chosen
  Repository? repo;
  bool starting = false;
  String? note;

  @override
  void initState() {
    super.initState();
    repo = widget.repo;
    final c = widget.client;
    if (repo == null && c != null) {
      starting = true;
      c.restore().then((_) {
        if (!mounted) return;
        setState(() { starting = false; if (c.signedIn) repo = ApiRepository(c); });
      });
    }
  }

  void _signedIn() => setState(() { repo = ApiRepository(widget.client!); note = null; });
  Future<void> _signOut([String? message]) async {
    await repo?.signOut();
    if (mounted) setState(() { repo = widget.repo; note = message; });
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jas iVision',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        themeMode: mode,
        home: starting
            ? const Scaffold(body: Loading(text: 'Starting Jas iVision'))
            : repo == null
                ? SignInPage(client: widget.client!, onDone: _signedIn, message: note)
                : AppScope(
                    onSignedOut: () => _signOut('Please sign in again.'),
                    child: Shell(repo: repo!, dark: mode == ThemeMode.dark, onTheme: (d) => setState(() => mode = d ? ThemeMode.dark : ThemeMode.light), onSignOut: _signOut)),
      );
}

class Shell extends StatefulWidget {
  const Shell({super.key, required this.repo, required this.dark, required this.onTheme, required this.onSignOut});
  final Repository repo;
  final bool dark;
  final ValueChanged<bool> onTheme;
  final VoidCallback onSignOut;
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  CameraItem? alertFor;
  int wizardKey = 0;
  late final Future<Me> me = widget.repo.me().catchError((_) => const Me('', '', ''));

  static const _dest = [
    (Icons.home_outlined, Icons.home_rounded, 'Home', 'How your business is doing right now'),
    (Icons.notifications_none_rounded, Icons.notifications_rounded, 'Alerts', 'What happened, and what you are watching for'),
    (Icons.videocam_outlined, Icons.videocam_rounded, 'Cameras', 'Every camera, and live view when you need it'),
    (Icons.insert_chart_outlined_rounded, Icons.insert_chart_rounded, 'Reports', 'Summaries sent to your email'),
    (Icons.settings_outlined, Icons.settings_rounded, 'Settings', 'People, appearance and your account'),
  ];

  Widget get page => switch (tab) {
        0 => HomePage(repo: widget.repo, onOpenAlerts: () => setState(() => tab = 1), onOpenCameras: () => setState(() => tab = 2)),
        1 => AlertsPage(key: ValueKey('alerts$wizardKey'), repo: widget.repo, startWizardFor: alertFor),
        2 => CamerasPage(repo: widget.repo, onAddAlert: (c) => setState(() { alertFor = c; wizardKey++; tab = 1; })),
        3 => ReportsPage(repo: widget.repo),
        _ => SettingsPage(repo: widget.repo, dark: widget.dark, onTheme: widget.onTheme, onSignOut: widget.onSignOut),
      };

  void _go(int i) => setState(() { tab = i; alertFor = null; });

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final t = Tok.of(context);
    final body = SelectionArea(child: AnimatedSwitcher(duration: const Duration(milliseconds: 220), switchInCurve: Curves.easeOut, transitionBuilder: (c, a) => FadeTransition(opacity: a, child: c), child: KeyedSubtree(key: ValueKey(tab), child: page)));
    if (wide) {
      return Scaffold(
        body: Row(children: [
          Container(
            width: 272,
            decoration: BoxDecoration(color: t.surface, border: Border(right: BorderSide(color: t.line))),
            child: SafeArea(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Padding(padding: EdgeInsets.fromLTRB(22, 26, 22, 22), child: Align(alignment: Alignment.centerLeft, child: BrandLogo(style: LogoStyle.compact, height: 52))),
                for (var i = 0; i < _dest.length; i++) _NavItem(icon: tab == i ? _dest[i].$2 : _dest[i].$1, label: _dest[i].$3, selected: tab == i, onTap: () => _go(i)),
                const Spacer(),
                FutureBuilder(future: me, builder: (context, snap) => _UserCard(me: snap.data)),
                Padding(padding: const EdgeInsets.fromLTRB(22, 4, 22, 18), child: Text(tagline, style: TextStyle(color: t.fg2.withValues(alpha: .85), fontSize: 11.5, fontStyle: FontStyle.italic))),
              ]),
            ),
          ),
          Expanded(
            child: Column(children: [
              _Header(title: _dest[tab].$3, subtitle: _dest[tab].$4, dark: widget.dark, onTheme: widget.onTheme),
              Expanded(child: Align(alignment: Alignment.topCenter, child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 1120), child: body))),
            ]),
          ),
        ]),
      );
    }
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 60,
        titleSpacing: 16,
        title: Row(children: [const BrandLogo(style: LogoStyle.mark, height: 34), const SizedBox(width: 12), Text(_dest[tab].$3)]),
        actions: [IconButton(tooltip: widget.dark ? 'Switch to light' : 'Switch to dark', icon: Icon(widget.dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined), onPressed: () => widget.onTheme(!widget.dark)), const SizedBox(width: 4)],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line)),
      ),
      body: body,
      bottomNavigationBar: Container(
        decoration: BoxDecoration(border: Border(top: BorderSide(color: t.line))),
        child: NavigationBar(
          selectedIndex: tab > 2 ? 3 : tab,
          onDestinationSelected: (i) => _go(i == 3 ? 4 : i),
          destinations: [
            for (final d in [_dest[0], _dest[1], _dest[2], _dest[4]]) NavigationDestination(icon: Icon(d.$1), selectedIcon: Icon(d.$2), label: d.$3 == 'Settings' ? 'More' : d.$3),
          ],
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({required this.icon, required this.label, required this.selected, required this.onTap});
  final IconData icon;
  final String label;
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
        label: label,
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            height: 48,
            padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(color: selected ? t.brandSoft : Colors.transparent, borderRadius: BorderRadius.circular(14)),
            child: Row(children: [
              AnimatedContainer(duration: const Duration(milliseconds: 180), width: 4, height: selected ? 22 : 0, margin: const EdgeInsets.only(right: 12, left: -6), decoration: BoxDecoration(color: t.brand, borderRadius: BorderRadius.circular(4))),
              Icon(icon, color: selected ? t.brand : t.fg2, size: 22),
              const SizedBox(width: 14),
              Text(label, style: TextStyle(color: selected ? t.brand : t.fg, fontWeight: selected ? FontWeight.w700 : FontWeight.w600, fontSize: 15)),
            ]),
          ),
        ),
      ),
    );
  }
}

class _UserCard extends StatelessWidget {
  const _UserCard({required this.me});
  final Me? me;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final m = me;
    if (m == null || m.name.isEmpty) return const SizedBox(height: 8);
    return Container(
      margin: const EdgeInsets.fromLTRB(14, 0, 14, 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(16)),
      child: Row(children: [
        Container(width: 40, height: 40, alignment: Alignment.center, decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle), child: Text(m.name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16))),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(m.name, style: const TextStyle(fontWeight: FontWeight.w700), maxLines: 1, overflow: TextOverflow.ellipsis), Text(m.business, style: TextStyle(color: t.fg2, fontSize: 12.5), maxLines: 1, overflow: TextOverflow.ellipsis)])),
      ]),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.title, required this.subtitle, required this.dark, required this.onTheme});
  final String title, subtitle;
  final bool dark;
  final ValueChanged<bool> onTheme;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      height: 88,
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Row(children: [
        Expanded(child: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)), Text(subtitle, style: TextStyle(color: t.fg2, fontSize: 14))])),
        IconButton.outlined(tooltip: dark ? 'Switch to light' : 'Switch to dark', icon: Icon(dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined), onPressed: () => onTheme(!dark)),
      ]),
    );
  }
}
