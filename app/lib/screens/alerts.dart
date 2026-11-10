import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'alert_rows.dart';
import 'common.dart';
import 'wizard.dart';

class AlertsPage extends StatefulWidget {
  const AlertsPage({super.key, required this.repo, this.startWizardFor, this.onChanged});
  final Repository repo;
  final CameraItem? startWizardFor;
  final VoidCallback? onChanged;
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

  void _reload() {
    setState(() { items = widget.repo.alerts(); });
    widget.onChanged?.call();
  }

  Future<void> _wizard({CameraItem? camera, AlertDef? edit}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, preselected: camera, edit: edit)));
    if (saved == true) {
      setState(() { mine = widget.repo.alertDefs(); items = widget.repo.alerts(); });
      widget.onChanged?.call();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pad = wide ? 32.0 : 16.0;
    return DefaultTabController(
      length: 2,
      child: Column(children: [
        Padding(
          padding: EdgeInsets.fromLTRB(pad, 0, pad, 0),
          child: Row(children: [
            Expanded(child: TabBar(isScrollable: true, tabAlignment: TabAlignment.start, tabs: const [Tab(text: 'Inbox'), Tab(text: 'My alerts')], labelColor: t.brand, unselectedLabelColor: t.fg2, indicatorColor: t.accent, indicatorWeight: 3, dividerColor: t.line)),
            const SizedBox(width: 12),
            Semantics(button: true, label: 'New alert', child: FilledButton.icon(style: FilledButton.styleFrom(minimumSize: const Size(44, 44), padding: const EdgeInsets.symmetric(horizontal: 16)), onPressed: _wizard, icon: const Icon(Icons.add_rounded, size: 20), label: Text(wide ? 'New alert' : 'New'))),
          ]),
        ),
        Expanded(
          child: TabBarView(children: [
            FutureBuilder(
              future: items,
              builder: (context, snap) {
                if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your alerts', text: '${snap.error}');
                if (!snap.hasData) return const Loading(text: 'Loading your alerts');
                final all = groupAlerts(snap.data!);
                final shown = all.where((g) => !openOnly || g.open).toList();
                final recent = shown.where((g) => isRecent(g.latest)).toList();
                final earlier = shown.where((g) => !isRecent(g.latest)).toList();
                final openCount = all.fold<int>(0, (s, g) => s + g.openItems.length);
                Widget list(List<AlertGroup> gs) => Group(children: [for (final g in gs) AlertRow(key: ValueKey('${g.key}${g.latest.id}'), group: g, repo: widget.repo, onChanged: _reload, swipe: true)]);
                return ListView(padding: EdgeInsets.fromLTRB(pad, 14, pad, navClear), children: [
                  Row(children: [
                    SegmentedButton<bool>(showSelectedIcon: false, segments: const [ButtonSegment(value: true, label: Text('Open')), ButtonSegment(value: false, label: Text('All'))], selected: {openOnly}, onSelectionChanged: (v) => setState(() => openOnly = v.first)),
                    Expanded(child: Align(alignment: Alignment.centerRight, child: openCount > 0 ? TextButton(onPressed: () async { for (final g in all) { await doneGroup(widget.repo, g); } _reload(); }, child: Text('Mark all done ($openCount)', overflow: TextOverflow.ellipsis)) : const SizedBox())),
                  ]),
                  if (shown.isEmpty) const EmptyState(icon: Icons.check_circle_outline_rounded, title: 'Nothing here', text: 'You are all caught up.'),
                  if (recent.isNotEmpty) ...[const GroupLabel('Last 24 hours'), list(recent)],
                  if (earlier.isNotEmpty) ...[const GroupLabel('Earlier'), list(earlier)],
                  if (MediaQuery.sizeOf(context).width < 600 && shown.any((g) => g.open)) Padding(padding: const EdgeInsets.only(top: 12), child: Center(child: Text('Swipe right for Done, left to snooze', style: TextStyle(color: t.fg2, fontSize: 12.5)))),
                ]);
              },
            ),
            FutureBuilder(
              future: mine,
              builder: (context, snap) {
                if (snap.hasError) return Center(child: Text('${snap.error}'));
                if (!snap.hasData) return const Loading();
                final on = snap.data!.where((d) => d.enabled).toList();
                final off = snap.data!.where((d) => !d.enabled && d.recipientIds.isNotEmpty).toList();
                Widget row(AlertDef m) => InkWell(
                      onTap: () => _wizard(edit: m),
                      child: Padding(
                        padding: EdgeInsets.fromLTRB(cardPad(context), 10, 10, 10),
                        child: Row(children: [
                          Badge2(iconFor(m), m.smart ? Level.ai : Level.ok, ai: m.smart, size: 42),
                          const SizedBox(width: 14),
                          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 8, children: [Text(m.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)), if (m.smart) const SmartTag()]),
                            Text(m.summary, style: TextStyle(color: t.fg2, fontSize: 13)),
                          ])),
                          Semantics(label: '${m.title} is ${m.enabled ? 'on' : 'off'}', child: Switch(value: m.enabled, onChanged: (v) async { await widget.repo.setEnabled(m, v); if (mounted) setState(() => mine = Future.value(snap.data!)); widget.onChanged?.call(); })),
                        ]),
                      ),
                    );
                return ListView(padding: EdgeInsets.fromLTRB(pad, 6, pad, navClear), children: [
                  if (on.isEmpty && off.isEmpty) const EmptyState(icon: Icons.notifications_none_rounded, title: 'No alerts on yet', text: 'Tap New to choose what you want to hear about.'),
                  if (on.isNotEmpty) ...[const GroupLabel('On'), Group(children: [for (final m in on) row(m)])],
                  if (off.isNotEmpty) ...[const GroupLabel('Off'), Group(children: [for (final m in off) row(m)])],
                ]);
              },
            ),
          ]),
        ),
      ]),
    );
  }
}
