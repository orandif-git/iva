import 'package:flutter/material.dart';
import 'dart:typed_data';

import '../save_file.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/profile_menu.dart';
import 'common.dart';
import '../push.dart';
import 'recorders.dart';
import '../widgets/motion.dart';

/// More: the two things you open now and then. Reports and Settings sit here so the bar stays at five.
class MorePage extends StatefulWidget {
  const MorePage({super.key, required this.repo, required this.onSignOut, this.push, this.me});
  final Me? me;
  final Repository repo;
  final void Function(String? message) onSignOut;
  final PushRegistration? push;
  @override
  State<MorePage> createState() => _MorePageState();
}

class _MorePageState extends State<MorePage> {
  int? scheduled;

  @override
  void initState() {
    super.initState();
    _count();
  }

  void _count() => widget.repo.schedules().then((l) { if (mounted) setState(() { scheduled = l.where((x) => x.enabled).length; }); }).catchError((_) {});

  Future<void> _open(Widget page) async {
    await Navigator.of(context).push<void>(MaterialPageRoute(builder: (_) => page));
    _count();
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget row(IconData icon, String title, String sub, VoidCallback onTap) => InkWell(
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.fromLTRB(cardPad(context), 14, 12, 14),
            child: Row(children: [
              Container(width: 40, height: 40, decoration: BoxDecoration(color: t.brandSoft, borderRadius: BorderRadius.circular(12)), child: Icon(icon, color: t.brand, size: 21)),
              const SizedBox(width: 14),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                Text(sub, style: TextStyle(color: t.fg2, fontSize: 12.5)),
              ])),
              Icon(Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6)),
            ]),
          ),
        );
    final n = scheduled;
    final reportsSub = n == null ? 'Sent to you on WhatsApp' : (n == 0 ? 'None scheduled yet' : '$n scheduled, sent on WhatsApp');
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760),
        child: ListView(padding: EdgeInsets.fromLTRB(16, 16, 16, navClear), children: [
          if (widget.me != null) ...[
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
              child: Row(children: [
                Container(width: 52, height: 52, alignment: Alignment.center, decoration: BoxDecoration(color: t.brand, shape: BoxShape.circle), child: Text(widget.me!.name.isEmpty ? '?' : widget.me!.name[0].toUpperCase(), style: TextStyle(color: Theme.of(context).colorScheme.onPrimary, fontWeight: FontWeight.w700, fontSize: 20))),
                const SizedBox(width: 14),
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(widget.me!.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16.5)),
                  Text([if (widget.me!.role.isNotEmpty) widget.me!.role == 'admin' ? 'Owner' : widget.me!.role[0].toUpperCase() + widget.me!.role.substring(1), if (widget.me!.business.isNotEmpty) widget.me!.business].join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
                ])),
              ]),
            ),
            const SizedBox(height: 14),
          ],
          Group(children: [
            row(Icons.bar_chart_rounded, 'Reports', reportsSub, () => _open(_ReportsScreen(widget.repo))),
            row(Icons.tune_rounded, 'Settings', 'Your plan, people and recorders', () => _open(SettingsPage(repo: widget.repo, onSignOut: widget.onSignOut, push: widget.push))),
          ]),
          const SizedBox(height: 14),
          OutlinedButton(style: OutlinedButton.styleFrom(foregroundColor: t.crit), onPressed: () => widget.onSignOut(null), child: const Text('Sign out')),
        ]),
      ),
    );
  }
}

class _ReportsScreen extends StatelessWidget {
  const _ReportsScreen(this.repo);
  final Repository repo;
  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Reports'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: Tok.of(context).line))),
        body: ReportsPage(repo: repo),
      );
}

/// Reports: the ones sent on WhatsApp on a schedule, and making one now. Download of a made report is in the current app for now.
/// How a downloaded report is saved. Tests swap it; the app uses the browser download (or the share sheet on a phone).
Future<void> Function(String name, Uint8List bytes, String mime) fileSaver = saveFile;

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

  Future<void> _download(MadeReport m) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final f = await widget.repo.downloadReport(m.id);
      await fileSaver(f.name, f.bytes, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      messenger.showSnackBar(SnackBar(content: Text('Downloaded ${f.name}')));
    } on ApiError catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(const SnackBar(content: Text('We could not save the file on this device.')));
    }
  }

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
        if (!snap.hasData) return const SkeletonPage(label: 'Loading your reports');
        final (schedules, people, made) = snap.data!;
        String names(ReportSchedule s) {
          final n = people.where((p) => s.recipientIds.contains(p.id)).map((p) => p.name.split(RegExp(r'[ ,(]')).first).toList();
          return n.isEmpty ? 'Nobody chosen yet' : 'to ${n.join(', ')}';
        }

        return ListView(padding: EdgeInsets.fromLTRB(pad, 14, pad, navClear), children: [
          Row(children: [
            Expanded(child: Text('Reports are Excel files. We send you a link on WhatsApp.', style: TextStyle(color: t.fg2, fontSize: 13.5))),
            const SizedBox(width: 12),
            FilledButton.icon(style: FilledButton.styleFrom(minimumSize: const Size(44, 44)), onPressed: () => _edit(null, people), icon: const Icon(Icons.add_rounded, size: 20), label: Text(wide ? 'Schedule a report' : 'Schedule')),
          ]),
          const GroupLabel('Sent on WhatsApp on a schedule'),
          if (schedules.isEmpty)
            const EmptyState(icon: Icons.schedule_send_outlined, title: 'No scheduled reports', text: 'Choose a report, a time and who gets it, and we send it on WhatsApp for you.')
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
              Segments<int>(items: const [(1, 'Yesterday'), (7, '7 days'), (30, '30 days')], value: days, onChanged: (v) => setState(() => days = v)),
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
                    if (m.ready) IconButton(tooltip: 'Download ${m.title}', onPressed: () => _download(m), icon: Icon(Icons.download_rounded, color: t.brand)),
                  ]),
                ),
            ]),
            Padding(padding: const EdgeInsets.fromLTRB(6, 8, 6, 0), child: Text('Scheduled reports arrive as a link on WhatsApp. Tap the arrow on a ready report to download it as a spreadsheet.', style: TextStyle(color: t.fg2, fontSize: 12.5))),
          ],
        ]);
      },
    );
  }
}

/// Create or change a scheduled report: what, how often, when, and who gets it on WhatsApp.
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
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(title: const Text('Delete this scheduled report?'), content: const Text('It will stop being sent. Reports already made are kept.'), actions: [TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep it')), FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Delete'))]));
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
            Segments<String>(items: const [('daily', 'Every day'), ('weekly', 'Every week'), ('monthly', 'Every month')], value: frequency, onChanged: (v) => setState(() => frequency = v)),
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
            const GroupLabel('Who gets it on WhatsApp'),
            if (widget.people.isEmpty) Text('Add people first. Reports are sent to their WhatsApp number.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
            for (final p in widget.people)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _Pick(on: who.contains(p.id), enabled: p.hasPhone, check: true, onTap: () => setState(() => who.contains(p.id) ? who.remove(p.id) : who.add(p.id)), icon: Icons.person_outline_rounded, title: p.name, sub: p.hasPhone ? p.phone : 'No mobile number, so no reports'),
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

/// Settings: who you are and your plan, who gets alerts, your recorders, how alerts reach you, and your account.
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.repo, required this.onSignOut, this.push});
  final Repository repo;
  final void Function(String? message) onSignOut;
  final PushRegistration? push;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late Future<(Me, List<Person>, List<RecorderItem>)> data = _load();
  bool changing = false;
  String? passwordNote;

  Future<(Me, List<Person>, List<RecorderItem>)> _load() async {
    final r = await Future.wait([widget.repo.me(), widget.repo.people(), widget.repo.recorders()]);
    return (r[0] as Me, r[1] as List<Person>, r[2] as List<RecorderItem>);
  }

  void _reload() => setState(() { data = _load(); });

  Future<void> _add() async {
    final ok = await showDialog<bool>(context: context, builder: (_) => _AddPerson(repo: widget.repo));
    if (ok == true) _reload();
  }

  Future<void> _addRecorder() async {
    await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => RecorderSetupPage(repo: widget.repo)));
    if (mounted) setState(() { data = _load(); });
  }

  Future<void> _openRecorder(RecorderItem r, bool canEdit) async {
    await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => RecorderPage(repo: widget.repo, recorder: r, canEdit: canEdit)));
    if (mounted) setState(() { data = _load(); });
  }

  Future<void> _remove(Person p) async {
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(title: Text('Remove ${p.name}?'), content: const Text('They will stop getting alerts and reports on WhatsApp.'), actions: [TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep')), FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Remove'))]));
    if (ok == true) {
      await widget.repo.deletePerson(p.id);
      _reload();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: SelectionArea(
            child: FutureBuilder(
              future: data,
              builder: (context, snap) {
                if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your settings', text: '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
                if (!snap.hasData) return const SkeletonPage(label: 'Loading your settings');
                final (me, people, recorders) = snap.data!;
                final used = me.camerasInUse, limit = me.cameraLimit;
                return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 40), children: [
                  // who you are, and the plan
                  Container(
                    padding: const EdgeInsets.all(18),
                    decoration: BoxDecoration(gradient: brandGradient, borderRadius: BorderRadius.circular(22)),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(children: [
                        Container(width: 52, height: 52, alignment: Alignment.center, decoration: BoxDecoration(color: Colors.white.withValues(alpha: .2), shape: BoxShape.circle), child: Text(me.name.isEmpty ? '?' : me.name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700))),
                        const SizedBox(width: 14),
                        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(me.name, style: const TextStyle(color: Colors.white, fontSize: 19, fontWeight: FontWeight.w700)), Text(me.business, style: TextStyle(color: Colors.white.withValues(alpha: .85), fontSize: 14)), if (me.role == 'admin') Padding(padding: const EdgeInsets.only(top: 6), child: Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3), decoration: BoxDecoration(color: Colors.white.withValues(alpha: .2), borderRadius: BorderRadius.circular(99)), child: const Text('Owner or manager', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600))))])),
                      ]),
                      if (me.plan != null || used != null) ...[
                        const SizedBox(height: 16),
                        Row(children: [
                          Expanded(child: Text(me.plan == null ? 'Cameras' : '${me.plan}', maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: Colors.white.withValues(alpha: .9), fontWeight: FontWeight.w600))),
                          if (used != null && limit != null) Text('$used of $limit cameras', style: TextStyle(color: Colors.white.withValues(alpha: .9), fontSize: 13)),
                        ]),
                        if (used != null && limit != null && limit > 0) Padding(padding: const EdgeInsets.only(top: 8), child: ClipRRect(borderRadius: BorderRadius.circular(6), child: LinearProgressIndicator(value: (used / limit).clamp(0.0, 1.0), minHeight: 7, color: Colors.white, backgroundColor: Colors.white.withValues(alpha: .25)))),
                      ],
                    ]),
                  ),
                  GroupLabel('People who get alerts', trailing: TextButton.icon(onPressed: _add, icon: const Icon(Icons.add_rounded, size: 18), label: const Text('Add'))),
                  if (people.isEmpty)
                    EmptyState(icon: Icons.person_add_alt_1_outlined, title: 'Nobody yet', text: 'Add people so alerts and reports have somewhere to go. They receive them on WhatsApp.', action: FilledButton.icon(onPressed: _add, icon: const Icon(Icons.add_rounded), label: const Text('Add a person')))
                  else
                    Group(children: [
                      for (final p in people)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 10, 6, 10),
                          child: Row(children: [
                            Container(width: 40, height: 40, alignment: Alignment.center, decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle), child: Text(p.name.isEmpty ? '?' : p.name[0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700))),
                            const SizedBox(width: 14),
                            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(p.name, style: const TextStyle(fontWeight: FontWeight.w700)), Text(p.hasPhone ? p.phone : 'No mobile number', style: TextStyle(color: p.hasPhone ? t.fg2 : t.warnInk, fontSize: 13))])),
                            if (p.hasPhone) const Pill('WhatsApp', Level.ok),
                            IconButton(tooltip: 'Remove ${p.name}', icon: Icon(Icons.delete_outline_rounded, color: t.fg2), onPressed: () => _remove(p)),
                          ]),
                        ),
                    ]),
                  const GroupLabel('Your recorders'),
                  if (recorders.isEmpty)
                    Group(children: [Padding(padding: const EdgeInsets.all(16), child: Text(me.role == 'admin' ? 'No recorder is connected yet. Add one and we will guide you through it.' : 'No recorder is connected yet. Ask the owner to add one.', style: TextStyle(color: t.fg2)))])
                  else
                    Group(children: [
                      for (final r in recorders)
                        InkWell(
                          onTap: () => _openRecorder(r, me.role == 'admin'),
                          child: Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                          child: Row(children: [
                            Badge2(Icons.dns_outlined, r.online ? Level.ok : (r.status == 'never' ? Level.mute : Level.bad), size: 40),
                            const SizedBox(width: 14),
                            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(r.name, style: const TextStyle(fontWeight: FontWeight.w700)), Text('${r.channels} ${r.channels == 1 ? 'camera' : 'cameras'}${r.seenAgo == null ? '' : ' · seen ${ago(r.seenAgo)}'}', style: TextStyle(color: t.fg2, fontSize: 13))])),
                            Pill(r.online ? 'Online' : (r.status == 'never' ? 'Not connected' : 'Offline'), r.online ? Level.ok : (r.status == 'never' ? Level.mute : Level.bad), dot: true),
                            Icon(Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6)),
                          ]),
                        ),
                        ),
                    ]),
                  if (me.role == 'admin') Padding(padding: const EdgeInsets.only(top: 10), child: OutlinedButton.icon(onPressed: _addRecorder, icon: const Icon(Icons.add_rounded), label: const Text('Add a recorder'))),
                  const GroupLabel('How alerts reach you'),
                  Group(children: [
                    _InfoLine(Icons.chat_outlined, 'WhatsApp', 'Alerts and reports arrive as WhatsApp messages. This is on for everyone above who has a mobile number.'),
                    _PushLine(push: widget.push),
                  ]),
                  const GroupLabel('Account'),
                  Group(children: [
                    InkWell(onTap: () => setState(() { changing = !changing; passwordNote = null; }), child: _Line(Icons.lock_outline_rounded, 'Change password', chevron: !changing, open: changing)),
                    AnimatedSize(duration: const Duration(milliseconds: 220), curve: Curves.easeOut, alignment: Alignment.topCenter, child: changing ? Padding(padding: const EdgeInsets.only(top: 8), child: ChangePasswordPanel(repo: widget.repo, onCancel: () => setState(() => changing = false), onDone: (others) => setState(() { changing = false; passwordNote = others ? 'Password changed. Other devices were signed out.' : 'Password changed.'; }))) : (passwordNote == null ? const SizedBox(width: double.infinity) : Padding(padding: const EdgeInsets.fromLTRB(16, 0, 16, 12), child: Row(children: [Icon(Icons.check_circle_rounded, color: t.okInk, size: 18), const SizedBox(width: 8), Expanded(child: Text(passwordNote!, style: TextStyle(color: t.okInk, fontWeight: FontWeight.w600, fontSize: 13.5)))])))),
                    InkWell(onTap: () { Navigator.of(context).pop(); widget.onSignOut(null); }, child: _Line(Icons.logout_rounded, 'Sign out', color: t.crit)),
                  ]),
                  const GroupLabel('More'),
                  Group(children: [
                    Padding(padding: const EdgeInsets.all(16), child: Text('Recorders, billing, and creating or changing reports are still in the current app at the main address. They move here next.', style: TextStyle(color: t.fg2))),
                  ]),
                  const SizedBox(height: 26),
                  Center(child: Column(children: [const BrandLogo(style: LogoStyle.compact, height: 36), const SizedBox(height: 8), Text(tagline, style: TextStyle(color: t.fg2, fontSize: 12.5, fontStyle: FontStyle.italic))])),
                ]);
              },
            ),
          ),
        ),
      ),
    );
  }
}

/// Push notifications on this device: off until the person turns them on (a browser only allows its permission box after a tap).
class _PushLine extends StatefulWidget {
  const _PushLine({required this.push});
  final PushRegistration? push;
  @override
  State<_PushLine> createState() => _PushLineState();
}

class _PushLineState extends State<_PushLine> {
  bool busy = false;
  String? note;

  Future<void> _turnOn() async {
    setState(() { busy = true; note = null; });
    final r = await widget.push!.enable();
    if (mounted) setState(() { busy = false; note = r; });
  }

  Future<void> _turnOff() async {
    await widget.push!.stop();
    if (mounted) setState(() => note = 'Notifications are off for this device.');
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final p = widget.push;
    if (p != null && !p.service.supported && p.service.hint == 'ios-install') {
      return const _InfoLine(Icons.notifications_active_outlined, 'Push notifications', 'On iPhone, notifications work from the Home Screen app. Tap the Share button in Safari, choose Add to Home Screen, then open Jas iVision from its icon and turn them on here.');
    }
    if (p == null || !p.service.supported) {
      return const _InfoLine(Icons.notifications_active_outlined, 'Push notifications', 'Not available in this browser. On Android, Chrome works; on iPhone, add Jas iVision to the Home Screen first.');
    }
    return ValueListenableBuilder<bool>(
      valueListenable: p.on,
      builder: (context, on, _) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.notifications_active_outlined, size: 22, color: on ? t.okInk : t.fg2),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text('Push notifications', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5)),
              Text(on ? 'On for this device, in addition to WhatsApp.' : 'Get new alerts on this device, in addition to WhatsApp.', style: TextStyle(color: t.fg2, fontSize: 13, height: 1.35)),
            ])),
            const SizedBox(width: 10),
            if (on) OutlinedButton(onPressed: _turnOff, child: const Text('Turn off')) else FilledButton(onPressed: busy ? null : _turnOn, child: Text(busy ? 'Turning on…' : 'Turn on')),
          ]),
          if (note != null) Padding(padding: const EdgeInsets.only(left: 36, top: 8), child: Text(note!, style: TextStyle(color: t.fg2, fontSize: 13))),
        ]),
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.icon, this.label, {this.color, this.chevron = false, this.open = false});
  final IconData icon;
  final String label;
  final Color? color;
  final bool chevron, open;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15), child: Row(children: [Icon(icon, size: 22, color: color ?? t.fg2), const SizedBox(width: 14), Expanded(child: Text(label, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15.5, color: color ?? t.fg))), if (chevron || open) Icon(open ? Icons.expand_less_rounded : Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6))]));
  }
}

class _InfoLine extends StatelessWidget {
  const _InfoLine(this.icon, this.title, this.text);
  final IconData icon;
  final String title, text;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(padding: const EdgeInsets.all(16), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Badge2(icon, Level.ok, size: 40), const SizedBox(width: 14), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w700)), const SizedBox(height: 2), Text(text, style: TextStyle(color: t.fg2, fontSize: 13.5, height: 1.4))]))]));
  }
}

/// Adds a person who will get alerts and reports on WhatsApp. They must have agreed to receive messages.
class _AddPerson extends StatefulWidget {
  const _AddPerson({required this.repo});
  final Repository repo;
  @override
  State<_AddPerson> createState() => _AddPersonState();
}

class _AddPersonState extends State<_AddPerson> {
  final name = TextEditingController(), phone = TextEditingController(), email = TextEditingController();
  bool agreed = false, busy = false;
  String? error;

  Future<void> _save() async {
    if (name.text.trim().isEmpty || phone.text.trim().isEmpty) return setState(() => error = 'Enter a name and a mobile number.');
    if (!agreed) return setState(() => error = 'Please confirm that this person has agreed to get WhatsApp messages from us.');
    setState(() { busy = true; error = null; });
    try {
      await widget.repo.addPerson(name.text.trim(), phone.text.trim(), email: email.text.trim());
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (e) {
      setState(() { busy = false; error = e.message; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return AlertDialog(
      title: const Text('Add a person'),
      content: SizedBox(
        width: 380,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(controller: name, textCapitalization: TextCapitalization.words, decoration: const InputDecoration(labelText: 'Name', prefixIcon: Icon(Icons.person_outline_rounded))),
            const SizedBox(height: 12),
            TextField(controller: phone, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Mobile number', helperText: 'The WhatsApp number, for example 98765 43210', prefixIcon: Icon(Icons.phone_outlined))),
            const SizedBox(height: 12),
            TextField(controller: email, keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(labelText: 'Email (optional)', prefixIcon: Icon(Icons.mail_outline_rounded))),
            const SizedBox(height: 8),
            CheckboxListTile(contentPadding: EdgeInsets.zero, controlAffinity: ListTileControlAffinity.leading, value: agreed, onChanged: (v) => setState(() => agreed = v ?? false), title: const Text('This person has agreed to get WhatsApp messages from us', style: TextStyle(fontSize: 14))),
            if (error != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
          ]),
        ),
      ),
      actions: [TextButton(onPressed: busy ? null : () => Navigator.of(context).pop(false), child: const Text('Cancel')), FilledButton(onPressed: busy ? null : _save, child: Text(busy ? 'Adding…' : 'Add'))],
    );
  }
}
