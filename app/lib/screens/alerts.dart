import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import 'common.dart';
import 'wizard.dart';

class AlertsPage extends StatefulWidget {
  const AlertsPage({super.key, required this.repo, this.startWizardFor});
  final Repository repo;
  final CameraItem? startWizardFor;
  @override
  State<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends State<AlertsPage> {
  late Future<List<AlertItem>> items = widget.repo.alerts();
  final List<SavedAlert> mine = [
    SavedAlert(AlertKinds.byId('camera_offline'), 'All cameras · after 15 min', true),
    SavedAlert(AlertKinds.byId('recorder_offline'), 'Both recorders · after 10 min', true),
    SavedAlert(AlertKinds.byId('queue_long'), 'Billing queue · over 6 people, 5 min wait', true),
    SavedAlert(AlertKinds.byId('people_in_area'), 'Aisle 1 · over 10 for 10 min', false),
  ];
  bool openOnly = true;

  @override
  void initState() {
    super.initState();
    if (widget.startWizardFor != null) WidgetsBinding.instance.addPostFrameCallback((_) => _wizard(camera: widget.startWizardFor));
  }

  void _reload() => setState(() => items = widget.repo.alerts());

  Future<void> _wizard({CameraItem? camera}) async {
    final saved = await Navigator.of(context).push<SavedAlert>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, preselected: camera)));
    if (saved != null) setState(() => mine.insert(0, saved));
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        floatingActionButton: FloatingActionButton.extended(onPressed: _wizard, icon: const Icon(Icons.add), label: const Text('New alert')),
        body: Column(children: [
          TabBar(tabs: const [Tab(text: 'Inbox'), Tab(text: 'My alerts')], labelColor: t.brand, indicatorColor: t.brand),
          Expanded(
            child: TabBarView(children: [
              FutureBuilder(
                future: items,
                builder: (context, snap) {
                  if (!snap.hasData) return const Center(child: CircularProgressIndicator());
                  final list = snap.data!.where((a) => !openOnly || a.state == AlertState.open).toList();
                  return ListView(padding: const EdgeInsets.all(16), children: [
                    Align(alignment: Alignment.centerLeft, child: SegmentedButton<bool>(segments: const [ButtonSegment(value: true, label: Text('Open')), ButtonSegment(value: false, label: Text('All'))], selected: {openOnly}, onSelectionChanged: (v) => setState(() => openOnly = v.first))),
                    const SizedBox(height: 10),
                    if (list.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('Nothing here. You are all caught up.')),
                    for (final a in list)
                      Card(
                        margin: const EdgeInsets.only(bottom: 10),
                        child: ExpansionTile(
                          shape: const Border(),
                          leading: Badge2(a.smart ? Icons.auto_awesome : Icons.warning_amber_rounded, a.smart ? Level.ai : Level.bad),
                          title: Text(a.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text('${a.where} · ${a.when}'),
                          trailing: Pill(switch (a.state) { AlertState.open => 'Open', AlertState.snoozed => 'Snoozed', AlertState.done => 'Done' }, a.state == AlertState.open ? Level.bad : Level.mute),
                          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                          expandedCrossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(a.body),
                            const SizedBox(height: 12),
                            Wrap(spacing: 8, children: [
                              FilledButton(onPressed: () async { await widget.repo.done(a.id); _reload(); }, child: const Text('Mark as done')),
                              OutlinedButton(onPressed: () async { await widget.repo.snooze(a.id); _reload(); }, child: const Text('Snooze 1 hour')),
                            ]),
                          ],
                        ),
                      ),
                  ]);
                },
              ),
              ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, 90), children: [
                for (final m in mine)
                  Card(
                    margin: const EdgeInsets.only(bottom: 10),
                    child: ListTile(
                      leading: Badge2(m.kind.icon, m.kind.smart ? Level.ai : Level.ok),
                      title: Row(children: [Flexible(child: Text(m.kind.name, style: const TextStyle(fontWeight: FontWeight.w600))), if (m.kind.smart) ...[const SizedBox(width: 8), const Pill('Smart', Level.ai, icon: Icons.auto_awesome)]]),
                      subtitle: Text(m.summary),
                      trailing: Switch(value: m.on, onChanged: (v) => setState(() => m.on = v)),
                    ),
                  ),
              ]),
            ]),
          ),
        ]),
      ),
    );
  }
}
