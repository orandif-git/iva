import 'package:flutter/material.dart';

import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/area_editor.dart';
import 'common.dart';

class AlertKind {
  final String id, name, desc;
  final bool smart, area;
  final IconData icon;
  const AlertKind(this.id, this.name, this.desc, this.icon, {this.smart = false, this.area = false});
}

class AlertKinds {
  static const all = [
    AlertKind('camera_offline', 'Camera offline', 'A camera stops sending pictures.', Icons.videocam_off_outlined),
    AlertKind('recorder_offline', 'Recorder offline', 'The recorder loses its link.', Icons.wifi_off),
    AlertKind('connection_unstable', 'Connection keeps dropping', 'The link goes up and down.', Icons.sync_problem_outlined),
    AlertKind('people_in_area', 'Too many people in an area', 'Warns when a marked area is crowded.', Icons.groups_outlined, smart: true, area: true),
    AlertKind('queue_long', 'Long queue', 'People wait too long at a counter.', Icons.format_list_numbered, smart: true, area: true),
    AlertKind('phone_use', 'Phone in use at the counter', 'Possible phone use in a marked area.', Icons.smartphone, smart: true, area: true),
  ];
  static AlertKind byId(String id) => all.firstWhere((k) => k.id == id);
}

class SavedAlert {
  SavedAlert(this.kind, this.summary, this.on);
  final AlertKind kind;
  final String summary;
  bool on;
}

/// Alert first, then camera: kind, alert, cameras, settings (with the drawable area), who hears about it, save.
class AlertWizard extends StatefulWidget {
  const AlertWizard({super.key, required this.repo, this.preselected});
  final Repository repo;
  final CameraItem? preselected;
  @override
  State<AlertWizard> createState() => _AlertWizardState();
}

class _AlertWizardState extends State<AlertWizard> {
  int step = 1;
  bool? smart;
  AlertKind? kind;
  final Set<String> picked = {};
  int people = 10, minutes = 10;
  final area = AreaController();
  final who = {'Priya (you)': true, 'Ravi, store manager': true, 'Store phone': false};
  bool told = false;
  List<CameraItem> cams = [];

  @override
  void initState() {
    super.initState();
    widget.repo.cameras().then((c) {
      if (mounted) setState(() => cams = c);
    });
    if (widget.preselected != null) picked.add(widget.preselected!.id);
  }

  bool get canContinue => switch (step) {
        2 => kind != null,
        3 => picked.isNotEmpty,
        4 => !(kind?.area ?? false) || area.valid,
        _ => true,
      };

  void next() => setState(() => step = step == 2 && kind != null && widget.preselected != null && picked.isNotEmpty ? 4 : step + 1);

  String get summary {
    final names = cams.where((c) => picked.contains(c.id)).map((c) => c.name).toList();
    final where = names.length == cams.length && cams.isNotEmpty ? 'All cameras' : names.join(', ');
    return kind!.area ? '$where · over $people for $minutes min' : '$where · after $minutes min';
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('New alert'), leading: IconButton(icon: const Icon(Icons.close), tooltip: 'Close', onPressed: () => Navigator.of(context).pop())),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: SelectionArea(child: ListView(padding: const EdgeInsets.all(16), children: [
            Row(children: [for (var i = 1; i <= 5; i++) Expanded(child: Container(height: 5, margin: const EdgeInsets.only(right: 5), decoration: BoxDecoration(color: i <= step ? t.brand : t.line, borderRadius: BorderRadius.circular(3))))]),
            const SizedBox(height: 6),
            Text(step == 6 ? 'Done' : 'Step $step of 5', style: TextStyle(color: t.fg2, fontWeight: FontWeight.w600)),
            const SizedBox(height: 10),
            ...switch (step) { 1 => _kind(t), 2 => _alert(t), 3 => _where(t), 4 => _settings(t), 5 => _who(t), _ => _done(t) },
            if (step > 1 && step < 6) Padding(padding: const EdgeInsets.only(top: 20), child: Row(children: [
              OutlinedButton(onPressed: () => setState(() => step -= 1), child: const Text('Back')),
              const Spacer(),
              if (step < 5) FilledButton(onPressed: canContinue ? next : null, child: const Text('Continue')) else FilledButton(onPressed: who.values.any((v) => v) ? () => setState(() => step = 6) : null, child: const Text('Save alert')),
            ])),
          ])),
        ),
      ),
    );
  }

  List<Widget> _kind(Tok t) => [
        const Text('What kind of alert?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        const SizedBox(height: 14),
        _Tile('System Alerts', 'Tell me when a camera or the connection has a problem, and when it is fixed.', Icons.check_rounded, Level.ok, () => setState(() { smart = false; step = 2; })),
        const SizedBox(height: 10),
        _Tile('Smart Alerts', 'Watch the picture for you: crowds, queues, phones at the counter.', Icons.auto_awesome, Level.ai, () => setState(() { smart = true; step = 2; })),
      ];

  List<Widget> _alert(Tok t) => [
        Text('Which ${smart == true ? 'Smart' : 'System'} Alert?', style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        const SizedBox(height: 14),
        for (final k in AlertKinds.all.where((k) => k.smart == (smart == true)))
          Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(k.name, k.desc, kind == k, () => setState(() { kind = k; }), leading: Badge2(k.icon, k.smart ? Level.ai : Level.ok))),
      ];

  List<Widget> _where(Tok t) => [
        const Text('Which cameras?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text(kind!.area ? 'Choose the cameras that can see the place you care about. You draw the exact area next.' : 'Choose where this alert should watch.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 10),
        TextButton(onPressed: () => setState(() => picked.length == cams.length ? picked.clear() : picked.addAll(cams.map((c) => c.id))), child: Text(picked.length == cams.length ? 'Clear all' : 'Select all')),
        for (final c in cams)
          Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(c.name, c.recorder, picked.contains(c.id), () => setState(() => picked.contains(c.id) ? picked.remove(c.id) : picked.add(c.id)), check: true, trailing: Pill(c.working ? 'Working' : 'Offline', c.working ? Level.ok : Level.bad))),
      ];

  List<Widget> _settings(Tok t) => [
        const Text('Set it up', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text('${kind!.name} on ${picked.length == 1 ? cams.firstWhere((c) => c.id == picked.first).name : '${picked.length} cameras'}.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 14),
        if (kind!.area) ...[
          const Text('Draw the area on the picture', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          AreaEditor(controller: area),
          const SizedBox(height: 18),
        ],
        _Stepper(kind!.area ? 'Alert when more than' : 'Tell me if it stays offline for', kind!.area ? people : minutes, kind!.area ? 'people' : 'minutes', (v) => setState(() => kind!.area ? people = v : minutes = v), min: 1, max: 60),
        if (kind!.area) _Stepper('for at least', minutes, 'minutes', (v) => setState(() => minutes = v), min: 1, max: 60),
        if (kind!.id == 'phone_use')
          _Choice('Our staff have been told this alert is on', 'Required. The alert says "possible phone use in this area". It never names a person.', told, () => setState(() => told = !told), check: true),
      ];

  List<Widget> _who(Tok t) => [
        const Text('Who hears about it?', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        Text('Alerts go to WhatsApp. On the installed app you also get a push alert.', style: TextStyle(color: t.fg2)),
        const SizedBox(height: 12),
        for (final e in who.entries) Padding(padding: const EdgeInsets.only(bottom: 10), child: _Choice(e.key, '', e.value, () => setState(() => who[e.key] = !e.value), check: true)),
        const SizedBox(height: 8),
        const Text('Check and save', style: TextStyle(fontWeight: FontWeight.w700)),
        Card(child: Padding(padding: const EdgeInsets.all(14), child: Column(children: [
          _Row('Alert', kind!.name),
          _Row('Where', summary.split(' · ').first),
          _Row('Rule', summary.contains(' · ') ? summary.split(' · ').last : ''),
          if (kind!.area) _Row('Area', '${area.points.length} corners'),
          _Row('Sent to', 'WhatsApp: ${who.entries.where((e) => e.value).map((e) => e.key.split(' ').first).join(', ')}'),
        ]))),
      ];

  List<Widget> _done(Tok t) => [
        const SizedBox(height: 30),
        Center(child: Badge2(Icons.check_rounded, Level.ok, size: 60)),
        const SizedBox(height: 12),
        const Center(child: Text('Your alert is on', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700))),
        Center(child: Text('${kind!.name} is watching. We will message you on WhatsApp.', style: TextStyle(color: t.fg2))),
        const SizedBox(height: 16),
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          OutlinedButton(onPressed: () => ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Test message sent to your WhatsApp'))), child: const Text('Send me a test message')),
          const SizedBox(width: 10),
          FilledButton(onPressed: () => Navigator.of(context).pop(SavedAlert(kind!, summary, true)), child: const Text('Back to My alerts')),
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
            decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: t.line, width: 2)),
            child: Row(children: [Badge2(icon, level, size: 44), const SizedBox(width: 14), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)), Text(sub, style: TextStyle(color: t.fg2))])), const Icon(Icons.chevron_right)])));
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
          Text(unit),
        ]),
      ]));
}

class _Row extends StatelessWidget {
  const _Row(this.k, this.v);
  final String k, v;
  @override
  Widget build(BuildContext context) => Padding(padding: const EdgeInsets.symmetric(vertical: 6), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [SizedBox(width: 90, child: Text(k, style: TextStyle(color: Tok.of(context).fg2))), Expanded(child: Text(v, style: const TextStyle(fontWeight: FontWeight.w600)))]));
}
