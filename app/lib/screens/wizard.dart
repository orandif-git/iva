import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/area_editor.dart';
import 'common.dart';

IconData iconFor(AlertDef d) => iconForType(d.type, smart: d.smart);

/// Alert first, then camera: kind, alert, where, settings (with the drawable area), who hears about it, save.
/// System alerts apply to the whole account, so they skip the camera step; area alerts are drawn on one camera.
class AlertWizard extends StatefulWidget {
  const AlertWizard({super.key, required this.repo, this.preselected, this.edit});
  final Repository repo;
  final CameraItem? preselected;
  final AlertDef? edit;
  @override
  State<AlertWizard> createState() => _AlertWizardState();
}

class _AlertWizardState extends State<AlertWizard> {
  int step = 1;
  bool? smart;
  AlertDef? def;
  CameraItem? camera;
  final area = AreaController();
  final values = <String, int>{};
  final who = <String, bool>{};
  bool told = false, saving = false, saved = false;
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
          step = def!.area ? 3 : (def!.settings.isEmpty ? 5 : 4);
        }
        camera = widget.preselected;
      });
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

  void next() => setState(() {
        if (step == 2) {
          step = hasWhere ? 3 : (hasSettings ? 4 : 5);
        } else if (step == 3) {
          step = 4;
          picture = null;
          _loadPicture();
        } else {
          step += 1;
        }
      });

  void back() => setState(() {
        if (step == 5) {
          step = hasSettings ? 4 : 2;
        } else if (step == 4) {
          step = hasWhere ? 3 : 2;
        } else {
          step -= 1;
        }
        if (widget.edit != null && step < 3) step = 3;
      });

  int get stepNo => switch (step) { 6 => 5, _ => step };

  Future<void> _save() async {
    setState(() { saving = true; error = null; });
    try {
      await widget.repo.saveAlert(NewAlert(def!, {for (final e in values.entries) e.key: e.value}, [for (final e in who.entries) if (e.value) e.key],
          cameraId: camera?.id, cameraName: camera?.name, area: def!.area ? [for (final p in area.points) p.toJson()] : null));
      if (mounted) setState(() { saving = false; saved = true; step = 6; });
    } catch (e) {
      if (mounted) setState(() { saving = false; error = e.toString(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(widget.edit != null ? 'Change alert' : 'New alert'), leading: IconButton(icon: const Icon(Icons.close), tooltip: 'Close', onPressed: () => Navigator.of(context).pop(saved))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: SelectionArea(
            child: ListView(padding: const EdgeInsets.all(16), children: [
              Row(children: [for (var i = 1; i <= 5; i++) Expanded(child: Container(height: 5, margin: const EdgeInsets.only(right: 5), decoration: BoxDecoration(color: i <= stepNo ? t.brand : t.line, borderRadius: BorderRadius.circular(3))))]),
              const SizedBox(height: 6),
              Text(step == 6 ? 'Done' : 'Step $stepNo of 5', style: TextStyle(color: t.fg2, fontWeight: FontWeight.w600)),
              const SizedBox(height: 10),
              if (defs.isEmpty) const Padding(padding: EdgeInsets.all(30), child: Loading()) else ...switch (step) { 1 => _kind(t), 2 => _alert(t), 3 => _where(t), 4 => _settings(t), 5 => _who(t), _ => _done(t) },
              if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
              if (defs.isNotEmpty && step > 1 && step < 6)
                Padding(
                    padding: const EdgeInsets.only(top: 20),
                    child: Row(children: [
                      if (!(widget.edit != null && step <= 3 || widget.edit != null && step == 4 && !hasWhere)) OutlinedButton(onPressed: back, child: const Text('Back')),
                      const Spacer(),
                      if (step < 5) FilledButton(onPressed: canContinue ? next : null, child: const Text('Continue')) else FilledButton(onPressed: who.values.any((v) => v) && !saving ? _save : null, child: Text(saving ? 'Saving…' : 'Save alert')),
                    ])),
            ]),
          ),
        ),
      ),
    );
  }

  List<Widget> _kind(Tok t) => [
        const Text('What kind of alert?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        const SizedBox(height: 14),
        _Tile('System Alerts', 'Tell me when a camera or the connection has a problem, and when it is fixed.', Icons.verified_outlined, Level.ok, () => setState(() { smart = false; step = 2; })),
        const SizedBox(height: 10),
        _Tile('Smart Alerts', 'Watch the picture for you: crowds, queues, phones at the counter.', Icons.insights_rounded, Level.ai, () => setState(() { smart = true; step = 2; })),
      ];

  List<Widget> _alert(Tok t) {
    final list = defs.where((d) => d.smart == (smart == true)).toList();
    return [
      Text('Which ${smart == true ? 'Smart' : 'System'} Alert?', style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
      const SizedBox(height: 14),
      if (list.isEmpty) Text('There are no ${smart == true ? 'Smart' : 'System'} Alerts available on your plan yet.', style: TextStyle(color: t.fg2)),
      for (final d in list) Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(d.title, d.description, def == d, () => setState(() => _choose(d)), leading: Badge2(iconFor(d), d.smart ? Level.ai : Level.ok, ai: d.smart))),
    ];
  }

  List<Widget> _where(Tok t) => [
        const Text('Which camera?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text('Choose the camera that can see the place you care about. You draw the exact area next.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 12),
        for (final c in cams) Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(c.name, c.recorder, camera?.id == c.id, () => setState(() => camera = c), trailing: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad))),
      ];

  List<Widget> _settings(Tok t) => [
        const Text('Set it up', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text(def!.area ? '${def!.title} on ${camera?.name ?? ''}.' : '${def!.title} applies to all your cameras and recorders.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 14),
        if (def!.area) ...[
          const Text('Draw the area on the picture', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          AreaEditor(controller: area, picture: picture == null ? null : Image.memory(picture!, fit: BoxFit.cover, gaplessPlayback: true)),
          const SizedBox(height: 18),
        ],
        for (final s in def!.settings) _Stepper(s.label, values[s.key] ?? s.def, s.unit, (v) => setState(() => values[s.key] = v), min: s.min, max: s.max),
        if (def!.type == 'phone_use') _Choice('Our staff have been told this alert is on', 'Required. The alert says "possible phone use in this area". It never names a person.', told, () => setState(() => told = !told), check: true),
      ];

  List<Widget> _who(Tok t) => [
        const Text('Who hears about it?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text('Alerts go to WhatsApp. On the installed app you also get a push alert.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 12),
        if (people.isEmpty) Text('Add people in Settings first. Nobody gets an alert until you do.', style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600)),
        for (final p in people) Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(p.name, p.phone, who[p.id] ?? false, () => setState(() => who[p.id] = !(who[p.id] ?? false)), check: true)),
        const SizedBox(height: 8),
        const Text('Check and save', style: TextStyle(fontWeight: FontWeight.w700)),
        Card(
            child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(children: [
                  _Row('Alert', def!.title),
                  _Row('Where', def!.area ? (camera?.name ?? '') : 'All cameras and recorders'),
                  for (final s in def!.settings) _Row(s.label, '${values[s.key] ?? s.def} ${s.unit}'),
                  if (def!.area) _Row('Area', '${area.points.length} corners'),
                  _Row('Sent to', 'WhatsApp: ${people.where((p) => who[p.id] ?? false).map((p) => _first(p.name)).join(', ')}'),
                ]))),
      ];

  List<Widget> _done(Tok t) => [
        const SizedBox(height: 30),
        const Center(child: Badge2(Icons.check_rounded, Level.ok, size: 64)),
        const SizedBox(height: 12),
        const Center(child: Text('Your alert is on', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700))),
        Center(child: Text('${def!.title} is watching. We will message you on WhatsApp.', style: TextStyle(color: t.fg2))),
        const SizedBox(height: 16),
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

class _Tile extends StatelessWidget {
  const _Tile(this.title, this.sub, this.icon, this.level, this.onTap);
  final String title, sub;
  final IconData icon;
  final Level level;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: level == Level.ai ? t.ai.withValues(alpha: .5) : t.line, width: 2), boxShadow: t.lift),
            child: Row(children: [
              Badge2(icon, level, size: 48, ai: level == Level.ai),
              const SizedBox(width: 16),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 8, children: [Text(title, style: const TextStyle(fontSize: 17.5, fontWeight: FontWeight.w700)), if (level == Level.ai) const SmartTag(label: 'AI')]), const SizedBox(height: 2), Text(sub, style: TextStyle(color: t.fg2))])),
              Icon(Icons.chevron_right_rounded, color: t.fg2),
            ])));
  }
}

class _Choice extends StatelessWidget {
  const _Choice(this.title, this.sub, this.on, this.onTap, {this.check = false, this.leading, this.trailing});
  final String title, sub;
  final bool on, check;
  final VoidCallback onTap;
  final Widget? leading, trailing;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Semantics(
      checked: check ? on : null,
      selected: !check ? on : null,
      button: true,
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(color: on ? t.brandSoft : t.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: on ? t.brand : t.line, width: 2)),
          child: Row(children: [
            if (check) ...[Icon(on ? Icons.check_box : Icons.check_box_outline_blank, color: on ? t.brand : t.fg2), const SizedBox(width: 12)],
            if (leading != null) ...[leading!, const SizedBox(width: 12)],
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w600)), if (sub.isNotEmpty) Text(sub, style: TextStyle(color: t.fg2, fontSize: 13))])),
            if (trailing != null) trailing!,
          ]),
        ),
      ),
    );
  }
}

class _Stepper extends StatelessWidget {
  const _Stepper(this.label, this.value, this.unit, this.onChanged, {required this.min, required this.max});
  final String label, unit;
  final int value, min, max;
  final ValueChanged<int> onChanged;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        Row(children: [
          IconButton.outlined(onPressed: value > min ? () => onChanged(value - 1) : null, icon: const Icon(Icons.remove), tooltip: 'Less'),
          SizedBox(width: 70, child: Center(child: Text('$value', style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700)))),
          IconButton.outlined(onPressed: value < max ? () => onChanged(value + 1) : null, icon: const Icon(Icons.add), tooltip: 'More'),
          const SizedBox(width: 10),
          Flexible(child: Text(unit)),
        ]),
      ]));
}

class _Row extends StatelessWidget {
  const _Row(this.k, this.v);
  final String k, v;
  @override
  Widget build(BuildContext context) => Padding(padding: const EdgeInsets.symmetric(vertical: 6), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [SizedBox(width: 130, child: Text(k, style: TextStyle(color: Tok.of(context).fg2))), Expanded(child: Text(v, style: const TextStyle(fontWeight: FontWeight.w600)))]));
}

/// "Ravi, store manager" becomes "Ravi"; "Priya (you)" becomes "Priya".
String _first(String name) => name.split(RegExp(r'[ ,(]')).first;
