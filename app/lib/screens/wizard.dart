import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/area_editor.dart';
import 'common.dart';
import '../widgets/motion.dart';

IconData iconFor(AlertDef d) => iconForType(d.type, smart: d.smart);

const _stages = ['Kind', 'Alert', 'Where', 'Set it up', 'Who and save'];

/// One plain sentence for what the alert will do, from its numbers.
String describe(AlertDef d, Map<String, int> v, {String? where}) {
  String n(String k, int fallback) => '${v[k] ?? fallback}';
  final place = where == null || where.isEmpty ? 'this area' : 'the area on $where';
  return switch (d.type) {
    'camera_offline' => 'Tell me when a camera stops sending pictures, and when it is back.',
    'recorder_offline' => 'Tell me when a recorder loses its connection, and when it is back.',
    'connection_unstable' => 'Tell me when a recorder drops its connection ${n('drops', 5)} times in an hour.',
    'people_in_area' => 'Alert me when more than ${n('limit', 10)} people are in $place for ${n('minutes', 10)} minutes or more.',
    'queue_long' => 'Alert me when more than ${n('length', 6)} people are waiting in $place for ${n('minutes', 5)} minutes or more.',
    'phone_use' => 'Alert me when a phone may be in use in $place for ${n('seconds', 30)} seconds or more. It never names a person.',
    'blocked_plate' => 'Alert me when a vehicle from my blocked list is seen.',
    'daily_summary' => 'Send me a short summary every evening.',
    _ => d.description,
  };
}

/// What the WhatsApp message will look like, so people know what to expect.
String sampleMessage(AlertDef d, {String? where}) {
  final w = where == null || where.isEmpty ? 'Front door' : where;
  return switch (d.type) {
    'camera_offline' => 'Camera offline\n$w · 4:10 pm\nNo picture for 15 minutes.',
    'recorder_offline' => 'Recorder offline\nStore recorder · 4:10 pm\nIt has not contacted us for 10 minutes.',
    'connection_unstable' => 'Unstable connection\nStore recorder · 4:10 pm\n6 drops in the last hour.',
    'people_in_area' => 'Area occupancy limit exceeded\n$w · 4:10 pm\nMore than 10 people for 10 minutes.',
    'queue_long' => 'Long queue\n$w · 4:10 pm\n9 people have waited over 5 minutes.',
    'phone_use' => 'Possible phone use\n$w · 4:10 pm\nA phone may be in use in this area.',
    'blocked_plate' => 'Blocked vehicle detected\nGate · 4:10 pm\nTN72 AB 1234',
    _ => '${d.title}\n$w · 4:10 pm',
  };
}

/// Alert first, then camera: kind, alert, where, settings (with the drawable area), who hears about it, save.
/// System alerts apply to the whole account, so they skip the camera step; area alerts are drawn on one camera.
class AlertWizard extends StatefulWidget {
  const AlertWizard({super.key, required this.repo, this.preselected, this.edit, this.editArea});
  final Repository repo;
  final CameraItem? preselected;
  final AlertDef? edit;
  final AreaInfo? editArea;      // an area that already exists: its camera, shape and numbers are loaded, and saving changes it
  @override
  State<AlertWizard> createState() => _AlertWizardState();
}

class _AlertWizardState extends State<AlertWizard> {
  int step = 1;
  bool? smart;
  AlertDef? def;
  CameraItem? camera;
  AreaController area = AreaController();
  final values = <String, int>{};
  final who = <String, bool>{};
  bool told = false, saving = false, saved = false, forward = true;
  bool quiet = false;           // quiet hours for this alert
  int quietFrom = 22, quietTo = 7;      // hours of the day: 10 pm to 7 am unless changed
  String? error;
  List<AlertDef> defs = [];
  List<CameraItem> cams = [];
  List<Person> people = [];
  Uint8List? picture;

  @override
  void initState() {
    super.initState();
    () async {
      final r = widget.repo;
      final res = await Future.wait([r.alertDefs(), r.cameras(), r.people()]);
      if (!mounted) return;
      setState(() {
        defs = res[0] as List<AlertDef>;
        cams = res[1] as List<CameraItem>;
        people = res[2] as List<Person>;
        for (final p in people) {
          who[p.id] = true;
        }
        if (widget.edit != null) {
          _choose(defs.firstWhere((d) => d.type == widget.edit!.type, orElse: () => widget.edit!));
          for (final p in people) {
            who[p.id] = def!.recipientIds.contains(p.id);
          }
          final qs = int.tryParse((def!.quietStart ?? '').split(':').first), qe = int.tryParse((def!.quietEnd ?? '').split(':').first);
          if (qs != null && qe != null) {
            quiet = true;
            quietFrom = qs;
            quietTo = qe;
          }
          step = def!.area ? 3 : (def!.settings.isEmpty ? 5 : 4);
        }
        camera = widget.preselected;
        final ea = widget.editArea;
        if (ea != null && widget.edit != null) {
          // Changing an area that is already there: start from what is saved (the camera, the shape, the numbers), not from a blank screen.
          camera = cams.where((c) => c.id == ea.cameraId).firstOrNull ?? CameraItem(ea.cameraId, ea.cameraName, '', true);
          area = AreaController(points: ea.points, closed: true);
          for (final e in ea.params.entries) {
            if (values.containsKey(e.key)) values[e.key] = e.value.toInt();
          }
          step = 4;
        }
      });
      if (widget.editArea != null && mounted) _loadPicture();
    }();
  }

  void _choose(AlertDef d) {
    def = d;
    smart = d.smart;
    values
      ..clear()
      ..addAll({for (final s in d.settings) s.key: (d.params[s.key]?.toInt() ?? s.def)});
  }

  Future<void> _loadPicture() async {
    final id = camera?.id;
    if (id == null) return;
    final b = await widget.repo.snapshot(id);
    if (mounted && camera?.id == id) setState(() => picture = b);
  }

  bool get hasWhere => def?.area ?? false;
  bool get hasSettings => (def?.settings.isNotEmpty ?? false) || hasWhere;

  bool get canContinue => switch (step) {
        2 => def != null,
        3 => camera != null,
        4 => (!(def?.area ?? false) || area.valid) && (def?.type != 'phone_use' || told),
        _ => true,
      };

  void _to(int s) => setState(() { forward = s > step; step = s; });

  void next() {
    if (step == 2) {
      _to(hasWhere ? 3 : (hasSettings ? 4 : 5));
    } else if (step == 3) {
      picture = null;
      _loadPicture();
      _to(4);
    } else {
      _to(step + 1);
    }
  }

  bool get canGoBack {
    if (widget.edit == null) return step > 1;
    return step == 5 || (step == 4 && hasWhere);
  }

  void back() {
    if (step == 5) {
      _to(hasSettings ? 4 : 2);
    } else if (step == 4) {
      _to(hasWhere ? 3 : 2);
    } else {
      _to(step - 1);
    }
  }

  int get stepNo => step == 6 ? 5 : step;

  Future<void> _save() async {
    setState(() { saving = true; error = null; });
    try {
      await widget.repo.saveAlert(NewAlert(def!, {for (final e in values.entries) e.key: e.value}, [for (final e in who.entries) if (e.value) e.key],
          cameraId: camera?.id, cameraName: camera?.name, area: def!.area ? [for (final p in area.points) p.toJson()] : null, areaId: widget.editArea?.id,
          quietStart: quiet && !def!.critical ? _hhmm(quietFrom) : null, quietEnd: quiet && !def!.critical ? _hhmm(quietTo) : null));
      if (mounted) setState(() { saving = false; saved = true; forward = true; step = 6; });
    } catch (e) {
      if (mounted) setState(() { saving = false; error = e.toString(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final loading = defs.isEmpty;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.edit != null ? 'Change alert' : 'New alert'),
        leading: IconButton(icon: const Icon(Icons.close), tooltip: 'Close', onPressed: () => Navigator.of(context).pop(saved)),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(4),
          child: TweenAnimationBuilder<double>(tween: Tween(end: step == 6 ? 1 : stepNo / 5), duration: const Duration(milliseconds: 350), curve: Curves.easeOutCubic, builder: (_, v, __) => LinearProgressIndicator(value: v, minHeight: 4, color: t.accent, backgroundColor: t.line)),
        ),
      ),
      body: Column(children: [
        Expanded(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: SelectionArea(
                child: AnimatedSwitcher(
                  duration: const Duration(milliseconds: 260),
                  switchInCurve: Curves.easeOutCubic,
                  transitionBuilder: (c, a) => FadeTransition(opacity: a, child: SlideTransition(position: Tween(begin: Offset(forward ? .06 : -.06, 0), end: Offset.zero).animate(a), child: c)),
                  child: ListView(
                    key: ValueKey(step),
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
                    children: [
                      if (step < 6) Padding(padding: const EdgeInsets.only(bottom: 10), child: Text('Step $stepNo of 5 · ${_stages[stepNo - 1]}', style: TextStyle(color: t.fg2, fontWeight: FontWeight.w600, fontSize: 13))),
                      if (loading) const SkeletonRows() else ...switch (step) { 1 => _kind(t), 2 => _alert(t), 3 => _where(t), 4 => _settings(t), 5 => _who(t), _ => _done(t) },
                      if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
        if (!loading && step > 1 && step < 6) _ActionBar(summary: def == null ? null : [def!.title, if (hasWhere && camera != null) camera!.name].join(' · '), canBack: canGoBack, onBack: back, last: step == 5, canGo: step < 5 ? canContinue : (who.values.any((v) => v) && !saving), busy: saving, onGo: step < 5 ? next : _save),
      ]),
    );
  }

  List<Widget> _kind(Tok t) => [
        const Text('What kind of alert?', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
        const SizedBox(height: 6),
        Text('Pick the kind first. You choose the details in a moment.', style: TextStyle(color: t.fg2, fontSize: 15)),
        const SizedBox(height: 18),
        _KindCard(title: 'System Alerts', sub: 'Tell me when a camera or the connection has a problem, and when it is fixed.', icon: Icons.verified_outlined, ai: false, examples: const ['A camera stops sending pictures', 'A recorder goes offline', 'The connection keeps dropping'], onTap: () => setState(() { smart = false; _to(2); })),
        const SizedBox(height: 14),
        _KindCard(title: 'Smart Alerts', sub: 'Watch the picture for you: crowds, queues, phones at the counter.', icon: Icons.visibility_outlined, ai: true, examples: const ['Too many people in an area', 'A queue is too long', 'A phone in use at the counter'], onTap: () => setState(() { smart = true; _to(2); })),
      ];

  List<Widget> _alert(Tok t) {
    final list = defs.where((d) => d.smart == (smart == true)).toList();
    return [
      Text('Which ${smart == true ? 'Smart' : 'System'} Alert?', style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
      const SizedBox(height: 6),
      Text('Choose one. You can add more later.', style: TextStyle(color: t.fg2, fontSize: 15)),
      const SizedBox(height: 18),
      if (list.isEmpty) Text('There are no ${smart == true ? 'Smart' : 'System'} Alerts available on your plan yet.', style: TextStyle(color: t.fg2)),
      for (final d in list)
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: _AlertChoice(def: d, on: def == d, onTap: () => setState(() => _choose(d)), preview: sampleMessage(d)),
        ),
    ];
  }

  List<Widget> _where(Tok t) => [
        const Text('Which camera?', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
        const SizedBox(height: 6),
        Text('Choose the camera that can see the place you care about. You draw the exact area next.', style: TextStyle(color: t.fg2, fontSize: 15)),
        const SizedBox(height: 18),
        LayoutBuilder(builder: (context, box) {
          final cols = box.maxWidth > 560 ? 3 : 2;
          return GridView.count(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), crossAxisCount: cols, mainAxisSpacing: 12, crossAxisSpacing: 12, childAspectRatio: 1.25, children: [
            for (final c in cams) _CameraTile(c: c, on: camera?.id == c.id, onTap: () => setState(() => camera = c)),
          ]);
        }),
      ];

  List<Widget> _settings(Tok t) => [
        const Text('Set it up', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
        const SizedBox(height: 6),
        Text(def!.area ? '${def!.title} on ${camera?.name ?? ''}.' : '${def!.title} applies to all your cameras and recorders.', style: TextStyle(color: t.fg2, fontSize: 15)),
        const SizedBox(height: 16),
        _Sentence(describe(def!, values, where: camera?.name)),
        const SizedBox(height: 18),
        if (def!.area) ...[
          const Text('Draw the area on the picture', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
          const SizedBox(height: 8),
          AreaEditor(controller: area, picture: picture == null ? null : Image.memory(picture!, fit: BoxFit.cover, gaplessPlayback: true)),
          const SizedBox(height: 22),
        ],
        for (final s in def!.settings)
          _Stepper(s.label, values[s.key] ?? s.def, s.unit, (v) => setState(() => values[s.key] = v), min: s.min, max: s.max,
              help: s.key == 'staff' ? 'For example the cashier. People who work inside the area you drew are left out, so the number above means customers only.' : s.key == 'picture_minutes' ? ((values[s.key] ?? s.def) == 0 ? 'No pictures will be taken for this alert.' : 'A picture is kept with the alert: one the moment the limit is reached, then another every ${values[s.key] ?? s.def} minutes while it stays that way. The alert shows the newest. People in the picture are not named.') : null),
        if (def!.type == 'phone_use') _Pick2(on: told, onTap: () => setState(() => told = !told), title: 'Our staff have been told this alert is on', sub: 'Required. The alert says "possible phone use in this area". It never names a person.'),
      ];

  List<Widget> _who(Tok t) => [
        const Text('Who hears about it?', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6)),
        const SizedBox(height: 6),
        Text('Alerts go to WhatsApp. On the installed app you also get a push alert.', style: TextStyle(color: t.fg2, fontSize: 15)),
        const SizedBox(height: 16),
        if (people.isEmpty) Text('Add people in Settings first. Nobody gets an alert until you do.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
        for (final p in people) Padding(padding: const EdgeInsets.only(bottom: 10), child: _Pick2(on: who[p.id] ?? false, onTap: () => setState(() => who[p.id] = !(who[p.id] ?? false)), title: p.name, sub: p.phone, avatar: p.name)),
        const GroupLabel('Quiet hours'),
        if (def!.critical)
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(color: t.critSoft, borderRadius: BorderRadius.circular(Radii.tile)),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.priority_high_rounded, color: t.crit, size: 20),
              const SizedBox(width: 10),
              Expanded(child: Text('This is an urgent alert, so it is always sent, day and night. It has no quiet hours.', style: TextStyle(color: t.crit, fontWeight: FontWeight.w600, fontSize: 14, height: 1.4))),
            ]),
          )
        else
          Container(
            decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 10, 12),
                child: Row(children: [
                  Container(width: 40, height: 40, decoration: BoxDecoration(color: t.brandSoft, borderRadius: BorderRadius.circular(12)), child: Icon(Icons.bedtime_outlined, color: t.brand, size: 21)),
                  const SizedBox(width: 12),
                  Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Do not message me at night', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                    Text(quiet ? 'No messages from ${_clockHour(quietFrom)} to ${_clockHour(quietTo)}' : 'Off: messages come at any time', style: TextStyle(color: t.fg2, fontSize: 12.5)),
                  ])),
                  Semantics(label: 'Quiet hours are ${quiet ? 'on' : 'off'}', child: Switch(value: quiet, onChanged: (v) => setState(() => quiet = v))),
                ]),
              ),
              if (quiet) ...[
                Divider(height: 1, color: t.line),
                _HourRow(label: 'Stop messages at', hour: quietFrom, onChanged: (h) => setState(() => quietFrom = h)),
                Divider(height: 1, color: t.line, indent: 16),
                _HourRow(label: 'Start again at', hour: quietTo, onChanged: (h) => setState(() => quietTo = h)),
              ],
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
                child: Text(
                    quiet
                        ? (quietFrom == quietTo
                            ? 'Choose two different times.'
                            : 'Between ${_clockHour(quietFrom)} and ${_clockHour(quietTo)} this alert sends no WhatsApp message and no push alert. Nothing is lost: it is still saved in the app, and you see it when you open it.')
                        : 'Turn this on if you do not want this alert to wake you. During quiet hours it is saved in the app but no message is sent.',
                    style: TextStyle(color: quiet && quietFrom == quietTo ? t.crit : t.fg2, fontSize: 13, height: 1.4)),
              ),
            ]),
          ),
        const GroupLabel('Check and save'),
        Section(
          title: def!.title,
          trailing: def!.smart ? const SmartTag() : null,
          subtitle: def!.area ? 'On ${camera?.name ?? ''}' : 'All cameras and recorders',
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(describe(def!, values, where: camera?.name), style: const TextStyle(fontSize: 15.5, height: 1.45)),
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 8, children: [
              if (def!.area) Pill('${area.points.length} corners drawn', Level.ok, icon: Icons.crop_free_rounded),
              Pill('WhatsApp: ${people.where((p) => who[p.id] ?? false).map((p) => _first(p.name)).join(', ')}', Level.mute, icon: Icons.chat_outlined),
              if (def!.critical) const Pill('Always sent', Level.bad, icon: Icons.priority_high_rounded) else if (quiet && quietFrom != quietTo) Pill('Quiet ${_clockHour(quietFrom)} to ${_clockHour(quietTo)}', Level.mute, icon: Icons.bedtime_outlined),
            ]),
          ]),
        ),
      ];

  List<Widget> _done(Tok t) => [
        const SizedBox(height: 28),
        Center(child: TweenAnimationBuilder<double>(tween: Tween(begin: .3, end: 1), duration: const Duration(milliseconds: 650), curve: Curves.elasticOut, builder: (_, v, c) => Transform.scale(scale: v, child: c), child: Container(width: 84, height: 84, decoration: BoxDecoration(color: t.okSoft, shape: BoxShape.circle), child: Icon(Icons.check_rounded, size: 46, color: t.okInk)))),
        const SizedBox(height: 18),
        const Center(child: Text('Your alert is on', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.6))),
        const SizedBox(height: 6),
        Center(child: Text('${def!.title} is watching. We will message you on WhatsApp.', textAlign: TextAlign.center, style: TextStyle(color: t.fg2, fontSize: 15))),
        const SizedBox(height: 22),
        _Bubble(sampleMessage(def!, where: camera?.name)),
        const SizedBox(height: 6),
        Center(child: Text('This is what an alert will look like.', style: TextStyle(color: t.fg2, fontSize: 12.5))),
        const SizedBox(height: 22),
        Wrap(alignment: WrapAlignment.center, spacing: 10, runSpacing: 10, children: [
          OutlinedButton(
              onPressed: () async {
                final to = people.where((p) => who[p.id] ?? false).firstOrNull;
                final m = ScaffoldMessenger.of(context);
                if (to == null) return;
                try {
                  await widget.repo.sendTest(def!.type, to.id);
                  m.showSnackBar(SnackBar(content: Text('Test message sent to ${_first(to.name)}')));
                } catch (e) {
                  m.showSnackBar(SnackBar(content: Text('$e')));
                }
              },
              child: const Text('Send me a test message')),
          FilledButton(onPressed: () => Navigator.of(context).pop(true), child: const Text('Back to My alerts')),
        ]),
      ];
}

/// "Ravi, store manager" becomes "Ravi"; "Priya (you)" becomes "Priya".
String _first(String name) => name.split(RegExp(r'[ ,(]')).first;

/// The fixed bar at the bottom: where you are, Back, and Continue or Save. It never scrolls away.
class _ActionBar extends StatelessWidget {
  const _ActionBar({required this.summary, required this.canBack, required this.onBack, required this.last, required this.canGo, required this.busy, required this.onGo});
  final String? summary;
  final bool canBack, last, canGo, busy;
  final VoidCallback onBack, onGo;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      decoration: BoxDecoration(color: t.surface, border: Border(top: BorderSide(color: t.line))),
      child: SafeArea(
        top: false,
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 760),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
              child: Row(children: [
                if (canBack) OutlinedButton(onPressed: onBack, child: const Text('Back')),
                if (canBack) const SizedBox(width: 12),
                Expanded(child: summary == null || MediaQuery.sizeOf(context).width < 520 ? const SizedBox() : Text(summary!, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: t.fg2, fontSize: 13))),
                FilledButton(style: FilledButton.styleFrom(minimumSize: const Size(120, 52)), onPressed: canGo ? onGo : null, child: Text(last ? (busy ? 'Saving…' : 'Save alert') : 'Continue')),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _KindCard extends StatelessWidget {
  const _KindCard({required this.title, required this.sub, required this.icon, required this.ai, required this.examples, required this.onTap});
  final String title, sub;
  final IconData icon;
  final bool ai;
  final List<String> examples;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      label: title,
      child: InkWell(
        borderRadius: BorderRadius.circular(22),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            gradient: ai ? LinearGradient(colors: [t.aiSoft, t.surface], begin: Alignment.topLeft, end: Alignment.bottomRight) : null,
            color: ai ? null : t.surface,
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: ai ? t.ai.withValues(alpha: .45) : t.line, width: 1.5),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Badge2(icon, ai ? Level.ai : Level.ok, size: 52, ai: ai),
              const SizedBox(width: 16),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 8, children: [Text(title, style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700, letterSpacing: -.3)), if (ai) const SmartTag(label: 'AI')]), const SizedBox(height: 2), Text(sub, style: TextStyle(color: t.fg2, height: 1.35))])),
              Icon(Icons.chevron_right_rounded, color: t.fg2),
            ]),
            const SizedBox(height: 14),
            for (final e in examples) Padding(padding: const EdgeInsets.only(bottom: 6), child: Row(children: [Icon(Icons.check_rounded, size: 16, color: ai ? t.ai : t.okInk), const SizedBox(width: 8), Expanded(child: Text(e, style: TextStyle(color: t.fg2, fontSize: 13.5)))])),
          ]),
        ),
      ),
    );
  }
}

/// An alert in the list. The chosen one opens up to show what the WhatsApp message will look like.
class _AlertChoice extends StatelessWidget {
  const _AlertChoice({required this.def, required this.on, required this.onTap, required this.preview});
  final AlertDef def;
  final bool on;
  final VoidCallback onTap;
  final String preview;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      selected: on,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(color: on ? t.brandSoft : t.surface, borderRadius: BorderRadius.circular(18), border: Border.all(color: on ? t.accent : t.line, width: on ? 2 : 1.5)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Badge2(iconFor(def), def.smart ? Level.ai : Level.ok, ai: def.smart, size: 44),
              const SizedBox(width: 14),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(def.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)), const SizedBox(height: 2), Text(def.description, style: TextStyle(color: t.fg2, fontSize: 13.5))])),
              Icon(on ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded, color: on ? t.brand : t.fg2),
            ]),
            AnimatedSize(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOut,
              alignment: Alignment.topCenter,
              child: on ? Padding(padding: const EdgeInsets.only(top: 14), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('WHAT YOU WILL GET ON WHATSAPP', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, letterSpacing: .5, color: t.fg2)), const SizedBox(height: 8), _Bubble(preview)])) : const SizedBox(width: double.infinity),
            ),
          ]),
        ),
      ),
    );
  }
}

/// A WhatsApp-style message, so the person knows what an alert looks like before they switch it on.
class _Bubble extends StatelessWidget {
  const _Bubble(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final lines = text.split('\n');
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 320),
        padding: const EdgeInsets.fromLTRB(12, 9, 12, 9),
        decoration: BoxDecoration(color: dark ? const Color(0xFF1F3B2D) : const Color(0xFFDCF8C6), borderRadius: const BorderRadius.only(topLeft: Radius.circular(4), topRight: Radius.circular(14), bottomLeft: Radius.circular(14), bottomRight: Radius.circular(14))),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text('Jas iVision', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: dark ? const Color(0xFF7FD6A0) : const Color(0xFF0B6B3A))),
          const SizedBox(height: 2),
          for (var i = 0; i < lines.length; i++) Text(lines[i], style: TextStyle(fontSize: 14, fontWeight: i == 0 ? FontWeight.w700 : FontWeight.w400, color: dark ? Colors.white : const Color(0xFF0B1220), height: 1.35)),
        ]),
      ),
    );
  }
}

class _CameraTile extends StatelessWidget {
  const _CameraTile({required this.c, required this.on, required this.onTap});
  final CameraItem c;
  final bool on;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      button: true,
      selected: on,
      label: '${c.name}, ${c.working ? 'working' : 'offline'}',
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          clipBehavior: Clip.antiAlias,
          decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(18), border: Border.all(color: on ? t.accent : t.line, width: on ? 2.5 : 1.5)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(
              child: Stack(children: [
                Positioned.fill(child: DecoratedBox(decoration: BoxDecoration(gradient: c.working ? const LinearGradient(colors: [Color(0xFF16304F), Color(0xFF2E5A8A)], begin: Alignment.topLeft, end: Alignment.bottomRight) : LinearGradient(colors: [t.surface2, t.line])))),
                Positioned.fill(child: CustomPaint(painter: FramePainter(color: (c.working ? Colors.white : t.fg2).withValues(alpha: .22), inset: 8, arm: 14, width: 2))),
                Center(child: Icon(Icons.videocam_rounded, size: 28, color: (c.working ? Colors.white : t.fg2).withValues(alpha: .5))),
                Positioned(left: 8, top: 8, child: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad, dot: true)),
                Positioned(right: 8, top: 8, child: AnimatedContainer(duration: const Duration(milliseconds: 180), width: 26, height: 26, decoration: BoxDecoration(color: on ? t.accent : Colors.white.withValues(alpha: .85), shape: BoxShape.circle, border: Border.all(color: on ? t.accent : t.line, width: 1.5)), child: on ? const Icon(Icons.check_rounded, size: 18, color: Colors.white) : null)),
              ]),
            ),
            Padding(padding: const EdgeInsets.fromLTRB(12, 8, 12, 10), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(c.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5), maxLines: 1, overflow: TextOverflow.ellipsis), Text(c.recorder, style: TextStyle(color: t.fg2, fontSize: 12), maxLines: 1, overflow: TextOverflow.ellipsis)])),
          ]),
        ),
      ),
    );
  }
}

/// The alert in one sentence, updated as the numbers change.
class _Sentence extends StatelessWidget {
  const _Sentence(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: t.brandSoft, borderRadius: BorderRadius.circular(16)),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(Icons.notes_rounded, color: t.brand, size: 22), const SizedBox(width: 12), Expanded(child: Text(text, style: TextStyle(fontSize: 15.5, height: 1.45, fontWeight: FontWeight.w600, color: t.fg)))]),
    );
  }
}

class _Pick2 extends StatelessWidget {
  const _Pick2({required this.on, required this.onTap, required this.title, required this.sub, this.avatar});
  final bool on;
  final VoidCallback onTap;
  final String title, sub;
  final String? avatar;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      checked: on,
      button: true,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 64),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(color: on ? t.brandSoft : t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: on ? t.accent : t.line, width: on ? 2 : 1.5)),
          child: Row(children: [
            if (avatar != null) ...[Container(width: 40, height: 40, alignment: Alignment.center, decoration: const BoxDecoration(gradient: brandGradient, shape: BoxShape.circle), child: Text(avatar!.isEmpty ? '?' : avatar![0].toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700))), const SizedBox(width: 14)],
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w700)), if (sub.isNotEmpty) Text(sub, style: TextStyle(color: t.fg2, fontSize: 13))])),
            Icon(on ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded, color: on ? t.brand : t.fg2),
          ]),
        ),
      ),
    );
  }
}

String _hhmm(int hour) => '${hour.toString().padLeft(2, '0')}:00';

/// "10 pm", "7 am", "12 noon", "12 midnight": an hour the way people say it.
String _clockHour(int h) => h == 0 ? '12 midnight' : (h == 12 ? '12 noon' : (h < 12 ? '$h am' : '${h - 12} pm'));

/// One hour of the day, changed with minus and plus (it goes round the clock).
class _HourRow extends StatelessWidget {
  const _HourRow({required this.label, required this.hour, required this.onChanged});
  final String label;
  final int hour;
  final ValueChanged<int> onChanged;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 10, 8),
        child: Row(children: [
          Expanded(child: Text(label, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15))),
          IconButton.outlined(onPressed: () => onChanged((hour + 23) % 24), icon: const Icon(Icons.remove), tooltip: '$label: one hour earlier'),
          SizedBox(width: 108, child: Center(child: Text(_clockHour(hour), style: display(20)))),
          IconButton.outlined(onPressed: () => onChanged((hour + 1) % 24), icon: const Icon(Icons.add), tooltip: '$label: one hour later'),
        ]),
      );
}

class _Stepper extends StatelessWidget {
  const _Stepper(this.label, this.value, this.unit, this.onChanged, {required this.min, required this.max, this.help});
  final String label, unit;
  final String? help;      // a sentence under the number, when the setting needs explaining
  final int value, min, max;
  final ValueChanged<int> onChanged;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: t.line, width: 1.5)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(label, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14.5)), Text(unit, style: TextStyle(color: t.fg2, fontSize: 13))])),
          IconButton.outlined(onPressed: value > min ? () => onChanged(value - 1) : null, icon: const Icon(Icons.remove), tooltip: 'Less'),
          SizedBox(width: 64, child: Center(child: Text('$value', style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, fontFeatures: [FontFeature.tabularFigures()])))),
          IconButton.outlined(onPressed: value < max ? () => onChanged(value + 1) : null, icon: const Icon(Icons.add), tooltip: 'More'),
        ]),
        if (help != null) Padding(padding: const EdgeInsets.only(top: 8, right: 4), child: Text(help!, style: TextStyle(color: t.fg2, fontSize: 13, height: 1.4))),
      ]),
    );
  }
}
