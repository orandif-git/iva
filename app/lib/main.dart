import 'package:flutter/material.dart';

import 'api_client.dart';
import 'models.dart';
import 'repo.dart';
import 'scope.dart';
import 'screens/more.dart';
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
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
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

  static const _dest = [
    (Icons.home_outlined, Icons.home, 'Home'),
    (Icons.notifications_none, Icons.notifications, 'Alerts'),
    (Icons.videocam_outlined, Icons.videocam, 'Cameras'),
    (Icons.bar_chart_outlined, Icons.bar_chart, 'Reports'),
    (Icons.settings_outlined, Icons.settings, 'Settings'),
  ];

  Widget get page => switch (tab) {
        0 => HomePage(repo: widget.repo, onOpenAlerts: () => setState(() => tab = 1), onOpenCameras: () => setState(() => tab = 2)),
        1 => AlertsPage(key: ValueKey('alerts$wizardKey'), repo: widget.repo, startWizardFor: alertFor),
        2 => CamerasPage(repo: widget.repo, onAddAlert: (c) => setState(() { alertFor = c; wizardKey++; tab = 1; })),
        3 => ReportsPage(repo: widget.repo),
        _ => SettingsPage(repo: widget.repo, dark: widget.dark, onTheme: widget.onTheme, onSignOut: widget.onSignOut),
      };

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final t = Tok.of(context);
    final title = _dest[tab].$3;
    // Text can be selected and copied on every screen (the drawing area and the camera picture opt out, so dragging there never selects text).
    final body = SelectionArea(child: page);
    if (wide) {
      return Scaffold(
        body: Row(children: [
          NavigationRail(
            extended: true,
            selectedIndex: tab,
            onDestinationSelected: (i) => setState(() { tab = i; alertFor = null; }),
            leading: Padding(padding: const EdgeInsets.fromLTRB(8, 16, 8, 16), child: Row(children: [Icon(Icons.center_focus_strong, color: t.brand), const SizedBox(width: 8), const Text('Jas iVision', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16))])),
            destinations: [for (final d in _dest) NavigationRailDestination(icon: Icon(d.$1), selectedIcon: Icon(d.$2), label: Text(d.$3))],
          ),
          VerticalDivider(width: 1, color: t.line),
          Expanded(child: Column(children: [
            Container(height: 64, alignment: Alignment.centerLeft, padding: const EdgeInsets.symmetric(horizontal: 24), child: Text(title, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700))),
            Expanded(child: Align(alignment: Alignment.topCenter, child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 1100), child: body))),
          ])),
        ]),
      );
    }
    return Scaffold(
      appBar: AppBar(title: Text(title, style: const TextStyle(fontWeight: FontWeight.w700))),
      body: body,
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab > 2 ? 3 : tab,
        onDestinationSelected: (i) => setState(() { tab = i == 3 ? 4 : i; alertFor = null; }),
        destinations: [
          for (final d in [_dest[0], _dest[1], _dest[2], _dest[4]]) NavigationDestination(icon: Icon(d.$1), selectedIcon: Icon(d.$2), label: d.$3 == 'Settings' ? 'More' : d.$3),
        ],
      ),
    );
  }
}
