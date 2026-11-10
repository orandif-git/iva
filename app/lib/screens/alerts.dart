import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
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
  late Future<List<AlertDef>> mine = widget.repo.alertDefs();
  bool openOnly = true;

  @override
  void initState() {
    super.initState();
    if (widget.startWizardFor != null) WidgetsBinding.instance.addPostFrameCallback((_) => _wizard(camera: widget.startWizardFor));
  }

  void _reload() => setState(() { items = widget.repo.alerts(); });

  Future<void> _wizard({CameraItem? camera, AlertDef? edit}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, preselected: camera, edit: edit)));
    if (saved == true) setState(() { mine = widget.repo.alertDefs(); items = widget.repo.alerts(); });
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
          TabBar(tabs: const [Tab(text: 'Inbox'), Tab(text: 'My alerts')], labelColor: t.brand, unselectedLabelColor: t.fg2, indicatorColor: t.brand, indicatorWeight: 3, dividerColor: t.line),
          Expanded(
            child: TabBarView(children: [
              FutureBuilder(
                future: items,
                builder: (context, snap) {
                  if (!snap.hasData) return const Loading();
                  final list = snap.data!.where((a) => !openOnly || a.state == AlertState.open).toList();
                  return ListView(padding: const EdgeInsets.all(16), children: [
                    Align(alignment: Alignment.centerLeft, child: SegmentedButton<bool>(segments: const [ButtonSegment(value: true, label: Text('Open')), ButtonSegment(value: false, label: Text('All'))], selected: {openOnly}, onSelectionChanged: (v) => setState(() => openOnly = v.first))),
                    const SizedBox(height: 10),
                    if (list.isEmpty) const EmptyState(icon: Icons.check_circle_outline_rounded, title: 'Nothing here', text: 'You are all caught up.'),
                    for (final a in list)
                      Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        child: ExpansionTile(
                          shape: const Border(),
                          leading: Badge2(a.smart ? Icons.insights_rounded : Icons.warning_amber_rounded, a.smart ? Level.ai : Level.bad, ai: a.smart),
                          title: Text(a.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                          subtitle: Text([a.where, a.when].where((x) => x.isNotEmpty).join(' · ')),
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
              FutureBuilder(
                future: mine,
                builder: (context, snap) {
                  if (snap.hasError) return Center(child: Text('${snap.error}'));
                  if (!snap.hasData) return const Loading();
                  final defs = snap.data!.where((d) => d.enabled).toList() + snap.data!.where((d) => !d.enabled && d.recipientIds.isNotEmpty).toList();
                  return ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, 90), children: [
                    if (defs.isEmpty) const EmptyState(icon: Icons.notifications_none_rounded, title: 'No alerts on yet', text: 'Tap New alert to choose what you want to hear about.'),
                    for (final m in defs)
                      Card(
                        margin: const EdgeInsets.only(bottom: 10),
                        child: ListTile(
                          onTap: () => _wizard(edit: m),
                          leading: Badge2(iconFor(m), m.smart ? Level.ai : Level.ok, ai: m.smart),
                          title: Row(children: [Flexible(child: Text(m.title, style: const TextStyle(fontWeight: FontWeight.w600))), if (m.smart) ...[const SizedBox(width: 8), const SmartTag()]]),
                          subtitle: Text(m.summary),
                          trailing: Semantics(label: '${m.title} is ${m.enabled ? 'on' : 'off'}', child: Switch(value: m.enabled, onChanged: (v) async { await widget.repo.setEnabled(m, v); if (mounted) setState(() => mine = Future.value(snap.data!)); })),
                        ),
                      ),
                  ]);
                },
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}
