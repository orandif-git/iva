import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

/// Reports: the ones emailed on a schedule, and making one now. Download of a made report is in the current app for now.
class ReportsPage extends StatefulWidget {
  const ReportsPage({super.key, required this.repo});
  final Repository repo;
  @override
  State<ReportsPage> createState() => _ReportsPageState();
}

class _ReportsPageState extends State<ReportsPage> {
  late Future<(List<ReportSchedule>, List<Person>, List<MadeReport>)> data = _load();
  String makeType = 'footfall_summary';
  int days = 7;
  bool making = false;
  String? message;

  Future<(List<ReportSchedule>, List<Person>, List<MadeReport>)> _load() async {
    final r = await Future.wait([widget.repo.schedules(), widget.repo.people(), widget.repo.madeReports()]);
    return (r[0] as List<ReportSchedule>, r[1] as List<Person>, r[2] as List<MadeReport>);
  }

  void _reload() => setState(() { data = _load(); });

  Future<void> _edit(ReportSchedule? s, List<Person> people) async {
    final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => ScheduleEditor(repo: widget.repo, schedule: s, people: people)));
    if (changed == true) _reload();
  }

  Future<void> _make() async {
    setState(() { making = true; message = null; });
    final to = DateTime.now().subtract(const Duration(days: 1)), from = to.subtract(Duration(days: days - 1));
    try {
      await widget.repo.makeReport(makeType, from, to);
      message = 'We are preparing it. It appears below when it is ready.';
      _reload();
    } on ApiError catch (e) {
      message = e.message;
    }
    if (mounted) setState(() => making = false);
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pad = wide ? 32.0 : 16.0;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your reports', text: '${snap.error}');
        if (!snap.hasData) return const Loading(text: 'Loading your reports');
        final (schedules, people, made) = snap.data!;
        String names(ReportSchedule s) {
          final n = people.where((p) => s.recipientIds.contains(p.id)).map((p) => p.name.split(RegExp(r'[ ,(]')).first).toList();
          return n.isEmpty ? 'Nobody chosen yet' : 'to ${n.join(', ')}';
        }

        return ListView(padding: EdgeInsets.fromLTRB(pad, 14, pad, navClear), children: [
          Row(children: [
            Expanded(child: Text('Reports are Excel files you can keep and share.', style: TextStyle(color: t.fg2, fontSize: 13.5))),
            const SizedBox(width: 12),
            FilledButton.icon(style: FilledButton.styleFrom(minimumSize: const Size(44, 44)), onPressed: () => _edit(null, people), icon: const Icon(Icons.add_rounded, size: 20), label: Text(wide ? 'Schedule a report' : 'Schedule')),
          ]),
          const GroupLabel('Emailed on a schedule'),
          if (schedules.isEmpty)
            const EmptyState(icon: Icons.schedule_send_outlined, title: 'No scheduled reports', text: 'Choose a report, a time and who gets it, and we email it for you.')
          else
            Group(children: [
              for (final s in schedules)
                InkWell(
                  onTap: () => _edit(s, people),
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(cardPad(context), 10, 10, 10),
                    child: Row(children: [
                      Badge2(reportTypes[s.type]?.$3 ?? Icons.description_outlined, s.enabled ? Level.ok : Level.mute, size: 42),
                      const SizedBox(width: 14),
                      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(s.title, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5, color: s.enabled ? t.fg : t.fg2)),
                        Text('${s.when} · ${names(s)}', style: TextStyle(color: t.fg2, fontSize: 13)),
                      ])),
                      Semantics(label: '${s.title} is ${s.enabled ? 'on' : 'off'}', child: Switch(value: s.enabled, onChanged: (v) async { s.enabled = v; try { await widget.repo.saveSchedule(s); } catch (_) { s.enabled = !v; } if (mounted) setState(() {}); })),
                    ]),
                  ),
                ),
            ]),
          const GroupLabel('Make one now'),
          Section(
            title: 'A report for a period',
            subtitle: 'Choose what you need. It is ready in a minute or two.',
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Wrap(spacing: 8, runSpacing: 8, children: [
                for (final e in reportTypes.entries) ChoiceChip(label: Text(e.value.$1), selected: makeType == e.key, onSelected: (_) => setState(() => makeType = e.key)),
              ]),
              const SizedBox(height: 14),
              SegmentedButton<int>(showSelectedIcon: false, segments: const [ButtonSegment(value: 1, label: Text('Yesterday')), ButtonSegment(value: 7, label: Text('7 days')), ButtonSegment(value: 30, label: Text('30 days'))], selected: {days}, onSelectionChanged: (v) => setState(() => days = v.first)),
              const SizedBox(height: 14),
              FilledButton.icon(onPressed: making ? null : _make, icon: const Icon(Icons.description_outlined), label: Text(making ? 'Starting…' : 'Make the report')),
              if (message != null) Padding(padding: const EdgeInsets.only(top: 10), child: Text(message!, style: TextStyle(color: t.fg2, fontSize: 13))),
            ]),
          ),
          if (made.isNotEmpty) ...[
            const GroupLabel('Recent reports'),
            Group(children: [
              for (final m in made)
                Padding(
                  padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
                  child: Row(children: [
                    Badge2(reportTypes[m.type]?.$3 ?? Icons.description_outlined, m.failed ? Level.bad : (m.ready ? Level.ok : Level.mute), size: 40),
                    const SizedBox(width: 14),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(m.title, style: const TextStyle(fontWeight: FontWeight.w700)), Text([m.period, if (m.rows != null) '${m.rows} rows'].where((x) => x.isNotEmpty).join(' · '), style: TextStyle(color: t.fg2, fontSize: 13))])),
                    Pill(m.failed ? 'Failed' : (m.ready ? 'Ready' : 'Preparing'), m.failed ? Level.bad : (m.ready ? Level.ok : Level.warn), dot: !m.ready),
                  ]),
                ),
            ]),
            Padding(padding: const EdgeInsets.fromLTRB(6, 8, 6, 0), child: Text('To download a ready report, open Reports in the current app for now.', style: TextStyle(color: t.fg2, fontSize: 12.5))),
          ],
        ]);
      },
    );
  }
}

/// Create or change a scheduled report: what, how often, when, and who gets it by email.
class ScheduleEditor extends StatefulWidget {
  const ScheduleEditor({super.key, required this.repo, required this.schedule, required this.people});
  final Repository repo;
  final ReportSchedule? schedule;
  final List<Person> people;
  @override
  State<ScheduleEditor> createState() => _ScheduleEditorState();
}

class _ScheduleEditorState extends State<ScheduleEditor> {
  late String type = widget.schedule?.type ?? 'footfall_summary';
  late String frequency = widget.schedule?.frequency ?? 'daily';
  late int weekday = widget.schedule?.weekday ?? 0;
  late int dayOfMonth = widget.schedule?.dayOfMonth ?? 1;
  late int hour = int.tryParse((widget.schedule?.sendAt ?? '07:00').split(':').first) ?? 7;
  late final Set<String> who = {...?widget.schedule?.recipientIds};
  bool busy = false;
  String? error;

  Future<void> _save() async {
    setState(() { busy = true; error = null; });
    try {
      await widget.repo.saveSchedule(ReportSchedule(id: widget.schedule?.id, type: type, frequency: frequency, sendAt: '${hour.toString().padLeft(2, '0')}:00', weekday: weekday, dayOfMonth: dayOfMonth, recipientIds: who.toList(), enabled: widget.schedule?.enabled ?? true));
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (e) {
      setState(() { busy = false; error = e.message; });
    }
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(title: const Text('Delete this scheduled report?'), content: const Text('It will stop being emailed. Reports already made are kept.'), actions: [TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep it')), FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Delete'))]));
    if (ok == true) {
      await widget.repo.deleteSchedule(widget.schedule!.id!);
      if (mounted) Navigator.of(context).pop(true);
    }
  }

  String get _clock => '${hour % 12 == 0 ? 12 : hour % 12} ${hour < 12 ? 'am' : 'pm'}';

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    return Scaffold(
      appBar: AppBar(title: Text(widget.schedule == null ? 'Schedule a report' : 'Change report'), leading: IconButton(icon: const Icon(Icons.close), tooltip: 'Close', onPressed: () => Navigator.of(context).pop(false)), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 40), children: [
            const GroupLabel('Which report'),
            for (final e in reportTypes.entries)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _Pick(on: type == e.key, onTap: () => setState(() => type = e.key), icon: e.value.$3, title: e.value.$1, sub: e.value.$2),
              ),
            const GroupLabel('How often'),
            SegmentedButton<String>(showSelectedIcon: false, segments: const [ButtonSegment(value: 'daily', label: Text('Every day')), ButtonSegment(value: 'weekly', label: Text('Every week')), ButtonSegment(value: 'monthly', label: Text('Every month'))], selected: {frequency}, onSelectionChanged: (v) => setState(() => frequency = v.first)),
            const SizedBox(height: 14),
            if (frequency == 'weekly') Wrap(spacing: 8, runSpacing: 8, children: [for (var i = 0; i < 7; i++) ChoiceChip(label: Text(days[i].substring(0, 3)), selected: weekday == i, onSelected: (_) => setState(() => weekday = i))]),
            if (frequency == 'monthly')
              Row(children: [
                IconButton.outlined(onPressed: dayOfMonth > 1 ? () => setState(() => dayOfMonth--) : null, icon: const Icon(Icons.remove), tooltip: 'Earlier day'),
                SizedBox(width: 110, child: Center(child: Text('Day $dayOfMonth', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)))),
                IconButton.outlined(onPressed: dayOfMonth < 28 ? () => setState(() => dayOfMonth++) : null, icon: const Icon(Icons.add), tooltip: 'Later day'),
              ]),
            const GroupLabel('Sent at'),
            Row(children: [
              IconButton.outlined(onPressed: hour > 0 ? () => setState(() => hour--) : null, icon: const Icon(Icons.remove), tooltip: 'Earlier'),
              SizedBox(width: 120, child: Center(child: Text(_clock, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)))),
              IconButton.outlined(onPressed: hour < 23 ? () => setState(() => hour++) : null, icon: const Icon(Icons.add), tooltip: 'Later'),
            ]),
            const GroupLabel('Who gets it by email'),
            if (widget.people.isEmpty) Text('Add people first. Reports are sent to their email address.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
            for (final p in widget.people)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _Pick(on: who.contains(p.id), enabled: p.hasEmail, check: true, onTap: () => setState(() => who.contains(p.id) ? who.remove(p.id) : who.add(p.id)), icon: Icons.person_outline_rounded, title: p.name, sub: p.hasEmail ? p.email! : 'No email address, so no reports'),
              ),
            if (error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
            const SizedBox(height: 18),
            FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(44, 54)), onPressed: busy || who.isEmpty ? null : _save, child: Text(busy ? 'Saving…' : 'Save')),
            if (widget.schedule != null) Padding(padding: const EdgeInsets.only(top: 10), child: TextButton(onPressed: busy ? null : _delete, child: Text('Delete this report', style: TextStyle(color: t.crit)))),
          ]),
        ),
      ),
    );
  }
}

class _Pick extends StatelessWidget {
  const _Pick({required this.on, required this.onTap, required this.icon, required this.title, required this.sub, this.check = false, this.enabled = true});
  final bool on, check, enabled;
  final VoidCallback onTap;
  final IconData icon;
  final String title, sub;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      selected: on,
      enabled: enabled,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: enabled ? onTap : null,
        child: Opacity(
          opacity: enabled ? 1 : .55,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            decoration: BoxDecoration(color: on ? t.brandSoft : t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: on ? t.accent : t.line, width: on ? 2 : 1.5)),
            child: Row(children: [
              Badge2(icon, on ? Level.ok : Level.mute, size: 40),
              const SizedBox(width: 14),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w700)), Text(sub, style: TextStyle(color: t.fg2, fontSize: 13))])),
              Icon(check ? (on ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded) : (on ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded), color: on ? t.brand : t.fg2),
            ]),
          ),
        ),
      ),
    );
  }
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
