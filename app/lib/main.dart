import 'package:flutter/material.dart';

import 'models.dart';
import 'repo.dart';
import 'screens/alerts.dart';
import 'screens/cameras.dart';
import 'screens/home.dart';
import 'theme.dart';

/// Run against a server with `--dart-define=API_BASE=https://host --dart-define=API_TOKEN=...`. Without them the app shows demo data.
const _base = String.fromEnvironment('API_BASE');
const _token = String.fromEnvironment('API_TOKEN');

void main() => runApp(IVisionApp(repo: _base.isEmpty ? MockRepository() : ApiRepository(_base, _token)));

class IVisionApp extends StatefulWidget {
  const IVisionApp({super.key, required this.repo});
  final Repository repo;
  @override
  State<IVisionApp> createState() => _IVisionAppState();
}

class _IVisionAppState extends State<IVisionApp> {
  ThemeMode mode = ThemeMode.light; // light by default, dark only when chosen

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Jas iVision',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        themeMode: mode,
        home: Shell(repo: widget.repo, dark: mode == ThemeMode.dark, onTheme: (d) => setState(() => mode = d ? ThemeMode.dark : ThemeMode.light)),
      );
}

class Shell extends StatefulWidget {
  const Shell({super.key, required this.repo, required this.dark, required this.onTheme});
  final Repository repo;
  final bool dark;
  final ValueChanged<bool> onTheme;
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
        3 => const Center(child: Text('Reports: scheduled summaries by email')),
        _ => _Settings(dark: widget.dark, onTheme: widget.onTheme),
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

class _Settings extends StatelessWidget {
  const _Settings({required this.dark, required this.onTheme});
  final bool dark;
  final ValueChanged<bool> onTheme;
  @override
  Widget build(BuildContext context) => ListView(padding: const EdgeInsets.all(16), children: [
        Card(child: ListTile(title: const Text('Appearance'), subtitle: const Text('Light is the default.'), trailing: SegmentedButton<bool>(segments: const [ButtonSegment(value: false, label: Text('Light')), ButtonSegment(value: true, label: Text('Dark'))], selected: {dark}, onSelectionChanged: (v) => onTheme(v.first)))),
      ]);
}
