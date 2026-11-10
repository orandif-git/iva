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
          return ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, navClear), children: [
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

/// People who get alerts, and where the rest of the settings live. Appearance, password and sign out are in the profile menu.
class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.repo});
  final Repository repo;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: SelectionArea(
            child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 40), children: [
              const GroupLabel('People who get alerts'),
              FutureBuilder(
                future: repo.people(),
                builder: (context, snap) {
                  if (snap.hasError) return Text('${snap.error}');
                  if (!snap.hasData) return const Padding(padding: EdgeInsets.all(24), child: Loading());
                  final list = snap.data!;
                  if (list.isEmpty) return const EmptyState(icon: Icons.person_add_alt_1_outlined, title: 'Nobody yet', text: 'Add people in the current app (More, Alerts) so alerts have somewhere to go. They receive alerts on WhatsApp.');
                  return Group(children: [
                    for (final p in list)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        child: Row(children: [
                          Container(width: 40, height: 40, alignment: Alignment.center, decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle), child: Text(p.name.isEmpty ? '?' : p.name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700))),
                          const SizedBox(width: 14),
                          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600)), Text(p.phone, style: TextStyle(color: t.fg2, fontSize: 13))])),
                          const Pill('WhatsApp', Level.ok),
                        ]),
                      ),
                  ]);
                },
              ),
              const GroupLabel('More'),
              Group(children: [
                Padding(padding: const EdgeInsets.all(16), child: Text('Recorders, billing, and creating or changing reports are still in the current app at the main address. They move here next.', style: TextStyle(color: t.fg2))),
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}
