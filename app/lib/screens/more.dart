import 'package:flutter/material.dart';

import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

class ReportsPage extends StatelessWidget {
  const ReportsPage({super.key, required this.repo});
  final Repository repo;
  @override
  Widget build(BuildContext context) => FutureBuilder(
        future: repo.reports(),
        builder: (context, snap) {
          if (snap.hasError) return Center(child: Text('${snap.error}'));
          if (!snap.hasData) return const Loading();
          final list = snap.data!;
          return ListView(padding: const EdgeInsets.all(16), children: [
            Section(
              title: 'Scheduled reports',
              subtitle: 'Sent by email on a schedule. Create and change them in the current app for now.',
              child: Column(children: [
                if (list.isEmpty) const Padding(padding: EdgeInsets.all(8), child: Text('You have no scheduled reports.')),
                for (final r in list) ListTile(contentPadding: EdgeInsets.zero, leading: const Badge2(Icons.description_outlined, Level.ok), title: Text(r.name, style: const TextStyle(fontWeight: FontWeight.w600)), subtitle: Text(r.when), trailing: Pill(r.on ? 'On' : 'Off', r.on ? Level.ok : Level.mute)),
              ]),
            ),
          ]);
        },
      );
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.repo, required this.dark, required this.onTheme, required this.onSignOut});
  final Repository repo;
  final bool dark;
  final ValueChanged<bool> onTheme;
  final VoidCallback onSignOut;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return ListView(padding: const EdgeInsets.all(16), children: [
      Section(
        title: 'People who get alerts',
        subtitle: 'They receive alerts on WhatsApp.',
        child: FutureBuilder(
          future: repo.people(),
          builder: (context, snap) {
            if (!snap.hasData) return const Padding(padding: EdgeInsets.all(8), child: LinearProgressIndicator());
            final list = snap.data!;
            return Column(children: [
              if (list.isEmpty) Text('Nobody yet. Add people in the current app (More, Alerts) so alerts have somewhere to go.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
              for (final p in list) ListTile(contentPadding: EdgeInsets.zero, leading: CircleAvatar(backgroundColor: t.brand, foregroundColor: Colors.white, child: Text(p.name.isEmpty ? '?' : p.name[0])), title: Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600)), subtitle: Text(p.phone)),
            ]);
          },
        ),
      ),
      const SizedBox(height: 14),
      Card(child: ListTile(title: const Text('Appearance'), subtitle: const Text('Light is the default.'), trailing: SegmentedButton<bool>(segments: const [ButtonSegment(value: false, label: Text('Light')), ButtonSegment(value: true, label: Text('Dark'))], selected: {dark}, onSelectionChanged: (v) => onTheme(v.first)))),
      const SizedBox(height: 14),
      Align(alignment: Alignment.centerLeft, child: OutlinedButton(onPressed: onSignOut, child: const Text('Sign out'))),
      const SizedBox(height: 10),
      Text('Recorders, billing and the other settings are still in the current app at the main address.', style: TextStyle(color: t.fg2, fontSize: 13)),
    ]);
  }
}
