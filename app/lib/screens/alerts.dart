import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'alert_rows.dart';
import 'common.dart';
import 'wizard.dart';
import '../widgets/motion.dart';

/// The alert inbox as a screen of its own (opened from the bell at the top right of every screen).
class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key, required this.repo, this.onChanged});
  final Repository repo;
  final VoidCallback? onChanged;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Alerts'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 900), child: AlertsPage(repo: repo, onChanged: onChanged))),
    );
  }
}

/// Alerts is for what HAPPENED: the inbox. Deciding what to watch for lives in the AI Watch tab.
class AlertsPage extends StatefulWidget {
  const AlertsPage({super.key, required this.repo, this.startWizardFor, this.onChanged, this.refresh});
  final Repository repo;
  final CameraItem? startWizardFor;
  final VoidCallback? onChanged;
  final Listenable? refresh;
  @override
  State<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends State<AlertsPage> {
  late Future<(List<AlertItem>, List<AlertDef>)> data = _load();
  bool openOnly = true;

  Future<(List<AlertItem>, List<AlertDef>)> _load() async {
    final r = await Future.wait([widget.repo.alerts(), widget.repo.alertDefs()]);
    return (r[0] as List<AlertItem>, r[1] as List<AlertDef>);
  }

  @override
  void initState() {
    super.initState();
    widget.refresh?.addListener(_reload);
    if (widget.startWizardFor != null) WidgetsBinding.instance.addPostFrameCallback((_) => _wizard(camera: widget.startWizardFor));
  }

  @override
  void dispose() {
    widget.refresh?.removeListener(_reload);
    super.dispose();
  }

  void _reload() {
    if (mounted) setState(() { data = _load(); });
    widget.onChanged?.call();
  }

  Future<void> _wizard({CameraItem? camera}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, preselected: camera)));
    if (saved == true) _reload();
  }


  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pad = wide ? 32.0 : 16.0;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your alerts', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
        if (!snap.hasData) return const SkeletonPage(label: 'Loading your alerts');
        final (items, defs) = snap.data!;
        final watching = defs.where((d) => d.enabled).length;
        final all = groupAlerts(items);
        final shown = all.where((g) => !openOnly || g.open).toList();
        final recent = shown.where((g) => isRecent(g.latest)).toList();
        final earlier = shown.where((g) => !isRecent(g.latest)).toList();
        final openCount = all.fold<int>(0, (s, g) => s + g.openItems.length);
        Widget list(List<AlertGroup> gs) => Group(children: [for (final g in gs) AlertRow(key: ValueKey('${g.key}${g.latest.id}'), group: g, repo: widget.repo, onChanged: _reload, swipe: true)]);
        return RefreshIndicator(
          onRefresh: () async => _reload(),
          child: ListView(padding: EdgeInsets.fromLTRB(pad, wide ? 0 : 14, pad, navClear), children: [
            Row(children: [
              SizedBox(width: 216, child: Segments<bool>(items: const [(true, 'Needs you'), (false, 'History')], value: openOnly, onChanged: (v) => setState(() => openOnly = v))),
              Expanded(child: Align(alignment: Alignment.centerRight, child: openOnly && openCount > 0 ? TextButton(onPressed: () async { for (final g in all) { await doneGroup(widget.repo, g); } _reload(); }, child: Text('Mark all done ($openCount)', overflow: TextOverflow.ellipsis)) : const SizedBox())),
            ]),
            if (shown.isEmpty)
              EmptyState(
                icon: openOnly ? Icons.check_circle_outline_rounded : Icons.history_rounded,
                title: openOnly ? 'Nothing needs you' : 'No alerts yet',
                text: openOnly ? (watching == 0 ? 'You are not watching for anything yet. Set up an alert and we will tell you on WhatsApp.' : 'You are watching for $watching ${watching == 1 ? 'thing' : 'things'}. We will message you on WhatsApp if one happens.') : 'When something happens it appears here.',
                action: watching == 0 ? FilledButton.icon(onPressed: () => _wizard(), icon: const Icon(Icons.add_rounded), label: const Text('Set up an alert')) : null,
              ),
            if (recent.isNotEmpty) ...[const GroupLabel('Last 24 hours'), list(recent)],
            if (earlier.isNotEmpty) ...[const GroupLabel('Earlier'), list(earlier)],
            if (MediaQuery.sizeOf(context).width < 600 && shown.any((g) => g.open)) Padding(padding: const EdgeInsets.only(top: 12), child: Center(child: Text('Swipe right for Done, left to snooze', style: TextStyle(color: t.fg2, fontSize: 12.5)))),
          ]),
        );
      },
    );
  }
}

/// Manage alerts: what you are watching for, switched on or off, and a way to add more.
class ManageAlertsPage extends StatefulWidget {
  const ManageAlertsPage({super.key, required this.repo, this.embedded = false});
  final Repository repo;
  final bool embedded;      // true when it is the AI Watch tab (no screen of its own, no back arrow)
  @override
  State<ManageAlertsPage> createState() => _ManageAlertsPageState();
}

class _ManageAlertsPageState extends State<ManageAlertsPage> {
  late Future<(List<AlertDef>, List<AreaInfo>)> mine = _load();
  bool smartTab = false;      // System alerts (recorders and cameras) or Smart alerts (what the AI sees)

  Future<(List<AlertDef>, List<AreaInfo>)> _load() async {
    final r = await Future.wait([widget.repo.alertDefs(), widget.repo.areas().catchError((_) => <AreaInfo>[])]);
    return (r[0] as List<AlertDef>, r[1] as List<AreaInfo>);
  }

  Future<void> _wizard({AlertDef? edit, AreaInfo? area}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, edit: edit, editArea: area)));
    if (saved == true && mounted) setState(() { mine = _load(); });
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final newAlert = FilledButton.icon(style: FilledButton.styleFrom(minimumSize: const Size(44, 44), padding: const EdgeInsets.symmetric(horizontal: 16), shape: const StadiumBorder()), onPressed: () => _wizard(), icon: const Icon(Icons.add_rounded, size: 20), label: const Text('New alert'));
    final list = _list(t);
    if (widget.embedded) {
      return Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Row(children: [
            Expanded(child: Semantics(header: true, child: Text('AI Watch', style: display(26)))),
            newAlert,
          ]),
        ),
        Expanded(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760), child: list))),
      ]);
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('Manage alerts'),
        actions: [Padding(padding: const EdgeInsets.only(right: 12), child: newAlert)],
        bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line)),
      ),
      body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760), child: list)),
    );
  }

  Widget _list(Tok t) {
    return Builder(
      builder: (context) => FutureBuilder(
            future: mine,
            builder: (context, snap) {
              if (snap.hasError) return Center(child: Text('${snap.error}'));
              if (!snap.hasData) return const Padding(padding: EdgeInsets.all(16), child: SkeletonRows());
              final defs = snap.data!.$1, areas = snap.data!.$2;
              final all = defs.where((d) => d.smart == smartTab).toList();
              final nSystem = defs.where((d) => !d.smart && d.enabled).length, nSmart = defs.where((d) => d.smart && d.enabled).length;
              final on = all.where((d) => d.enabled).toList();
              final off = all.where((d) => !d.enabled && d.recipientIds.isNotEmpty).toList();
              // The marked areas an alert is watching, one line each: tap one to change it (its shape and numbers are loaded).
              Widget areaLines(AlertDef m) {
                final mine = areas.where((a) => a.kind == m.areaKind).toList();
                if (!m.area || mine.isEmpty) return const SizedBox.shrink();
                return Column(children: [
                  for (final a in mine)
                    InkWell(
                      onTap: () => _wizard(edit: m, area: a),
                      child: Padding(
                        padding: EdgeInsets.fromLTRB(cardPad(context) + 56, 6, 12, 6),
                        child: Row(children: [
                          Icon(Icons.crop_free_rounded, size: 18, color: t.fg2),
                          const SizedBox(width: 10),
                          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(a.cameraName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                            Text(m.sentence(a.params).replaceFirst(RegExp(r'^Alert when '), 'When '), style: TextStyle(color: t.fg2, fontSize: 12.5)),
                          ])),
                          Icon(Icons.chevron_right_rounded, size: 20, color: t.fg2.withValues(alpha: .6)),
                        ]),
                      ),
                    ),
                  Padding(padding: EdgeInsets.fromLTRB(cardPad(context) + 46, 0, 12, 8), child: Align(alignment: Alignment.centerLeft, child: TextButton.icon(onPressed: () => _wizard(edit: m), icon: const Icon(Icons.add_rounded, size: 18), label: const Text('Add another area')))),
                ]);
              }
              Widget rowHead(AlertDef m) => InkWell(
                    onTap: () => m.area && areas.any((a) => a.kind == m.areaKind) ? null : _wizard(edit: m),
                    child: Padding(
                      padding: EdgeInsets.fromLTRB(cardPad(context), 10, 10, 10),
                      child: Row(children: [
                        Badge2(iconFor(m), m.smart ? Level.ai : Level.ok, ai: m.smart, size: 42),
                        const SizedBox(width: 14),
                        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 8, children: [Text(m.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)), if (m.smart) const SmartTag()]),
                          Text(m.summary, style: TextStyle(color: t.fg2, fontSize: 13)),
                        ])),
                        Semantics(label: '${m.title} is ${m.enabled ? 'on' : 'off'}', child: Switch(value: m.enabled, onChanged: (v) async { await widget.repo.setEnabled(m, v); if (mounted) setState(() { mine = Future.value(snap.data!); }); })),
                      ]),
                    ),
                  );
              Widget row(AlertDef m) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [rowHead(m), areaLines(m)]);
              return ListView(padding: EdgeInsets.fromLTRB(16, 6, 16, widget.embedded ? navClear : 40), children: [
                SizedBox(
                  width: double.infinity,
                  child: Segments<bool>(
                    items: [(false, 'System alerts ($nSystem)'), (true, 'Smart alerts ($nSmart)')],
                    value: smartTab,
                    onChanged: (v) => setState(() { smartTab = v; }),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(4, 10, 4, 0),
                  child: Text(smartTab ? 'The AI watches your cameras for people, vehicles and queues in the places you mark.' : 'We tell you when a recorder or camera has a problem.', style: TextStyle(color: t.fg2, fontSize: 13.5, height: 1.35)),
                ),
                if (on.isEmpty && off.isEmpty) EmptyState(icon: smartTab ? Icons.auto_awesome_rounded : Icons.notifications_none_rounded, title: smartTab ? 'No smart alerts yet' : 'No system alerts on', text: 'Choose what you want to hear about. We message you on WhatsApp.', action: FilledButton.icon(onPressed: () => _wizard(), icon: const Icon(Icons.add_rounded), label: const Text('New alert'))),
                if (on.isNotEmpty) ...[const GroupLabel('On'), Group(children: [for (final m in on) row(m)])],
                if (off.isNotEmpty) ...[const GroupLabel('Off'), Group(children: [for (final m in off) row(m)])],
              ]);
            },
          ),
    );
  }
}
