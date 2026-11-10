import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'alert_rows.dart';
import 'common.dart';
import 'wizard.dart';

/// Alerts is for what HAPPENED: the inbox. Deciding what to watch for is a separate place (Manage alerts), reached from the card at the top.
class AlertsPage extends StatefulWidget {
  const AlertsPage({super.key, required this.repo, this.startWizardFor, this.onChanged, this.refresh, required this.onOpenWatch});
  final Repository repo;
  final VoidCallback onOpenWatch;      // the AI Watch tab: what you are watching for
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

  void _manage() => widget.onOpenWatch();

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pad = wide ? 32.0 : 16.0;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your alerts', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
        if (!snap.hasData) return const Loading(text: 'Loading your alerts');
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
            _WatchingCard(watching: watching, onManage: _manage),
            const SizedBox(height: 16),
            Row(children: [
              SegmentedButton<bool>(showSelectedIcon: false, segments: const [ButtonSegment(value: true, label: Text('Needs you')), ButtonSegment(value: false, label: Text('History'))], selected: {openOnly}, onSelectionChanged: (v) => setState(() => openOnly = v.first)),
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

/// "What am I watching for?" in one tap, so the inbox and the settings are never mixed up.
class _WatchingCard extends StatelessWidget {
  const _WatchingCard({required this.watching, required this.onManage});
  final int watching;
  final VoidCallback onManage;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      label: 'Manage alerts. You are watching for $watching things.',
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onManage,
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
          decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(18), border: Border.all(color: t.line)),
          child: Row(children: [
            Container(width: 44, height: 44, decoration: BoxDecoration(color: t.brandSoft, borderRadius: BorderRadius.circular(14)), child: Icon(Icons.tune_rounded, color: t.brand)),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(watching == 0 ? 'Not watching for anything yet' : 'Watching for $watching ${watching == 1 ? 'thing' : 'things'}', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
              Text('Choose what you hear about', style: TextStyle(color: t.fg2, fontSize: 13)),
            ])),
            Text('Manage', style: TextStyle(color: t.brand, fontWeight: FontWeight.w700)),
            Icon(Icons.chevron_right_rounded, color: t.brand),
          ]),
        ),
      ),
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
  late Future<List<AlertDef>> mine = widget.repo.alertDefs();

  Future<void> _wizard({AlertDef? edit}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => AlertWizard(repo: widget.repo, edit: edit)));
    if (saved == true && mounted) setState(() { mine = widget.repo.alertDefs(); });
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final newAlert = FilledButton.icon(style: FilledButton.styleFrom(minimumSize: const Size(44, 40), padding: const EdgeInsets.symmetric(horizontal: 14)), onPressed: () => _wizard(), icon: const Icon(Icons.add_rounded, size: 20), label: const Text('New alert'));
    final list = _list(t);
    if (widget.embedded) {
      return Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 16, 4),
          child: Row(children: [
            Expanded(child: Text('AI Watch', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.4))),
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
                        Semantics(label: '${m.title} is ${m.enabled ? 'on' : 'off'}', child: Switch(value: m.enabled, onChanged: (v) async { await widget.repo.setEnabled(m, v); if (mounted) setState(() { mine = Future.value(snap.data!); }); })),
                      ]),
                    ),
                  );
              return ListView(padding: EdgeInsets.fromLTRB(16, 6, 16, widget.embedded ? navClear : 40), children: [
                if (on.isEmpty && off.isEmpty) EmptyState(icon: Icons.notifications_none_rounded, title: 'No alerts on yet', text: 'Choose what you want to hear about. We message you on WhatsApp.', action: FilledButton.icon(onPressed: () => _wizard(), icon: const Icon(Icons.add_rounded), label: const Text('New alert'))),
                if (on.isNotEmpty) ...[const GroupLabel('On'), Group(children: [for (final m in on) row(m)])],
                if (off.isNotEmpty) ...[const GroupLabel('Off'), Group(children: [for (final m in off) row(m)])],
              ]);
            },
          ),
    );
  }
}
