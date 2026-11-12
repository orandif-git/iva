import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models.dart';
import '../repo.dart';
import '../state/app_state.dart';
import '../theme.dart';
import '../widgets/motion.dart';
import 'common.dart';

/// The rules a customer built: several of one kind, each for a camera, a marked area, or the whole account.
///
/// The old list of alert types is still the account-wide default. This screen is where a shop with more than one camera
/// says "this queue, at this till, is long at six; that queue by the gate is different".

/// Everything the rules screens need, read once.
class RulesData {
  final List<Rule> rules;
  final List<AlertDef> defs;
  final List<CameraItem> cameras;
  final List<AreaInfo> areas;
  final List<Person> people;
  const RulesData(this.rules, this.defs, this.cameras, this.areas, this.people);

  AlertDef? defFor(String type) => defs.where((d) => d.type == type).firstOrNull;
  CameraItem? cameraFor(String? id) => id == null ? null : cameras.where((c) => c.id == id).firstOrNull;
  AreaInfo? areaFor(String? id) => id == null ? null : areas.where((a) => a.id == id).firstOrNull;
  String placeOf(Rule r) {
    if (r.zoneId != null) {
      final a = areaFor(r.zoneId);
      if (a != null) return '${a.cameraName} · ${areaWord(a.kind)}';
    }
    if (r.cameraId != null) return cameraFor(r.cameraId)?.name ?? 'A camera';
    return 'Every camera';
  }
}

String areaWord(String kind) => switch (kind) {
      'queue_area' => 'Queue',
      'area_count' => 'People in an area',
      'staff_area' => 'Staff area',
      _ => 'Marked area',
    };

final rulesDataProvider = FutureProvider.autoDispose<RulesData>((ref) async {
  final repo = ref.watch(repositoryProvider);
  final r = await Future.wait<Object>([
    repo.rules(),
    repo.alertDefs(),
    repo.cameras(),
    repo.areas().catchError((_) => <AreaInfo>[]),
    repo.people(),
  ]);
  return RulesData(r[0] as List<Rule>, r[1] as List<AlertDef>, r[2] as List<CameraItem>, r[3] as List<AreaInfo>, r[4] as List<Person>);
});

/// The list, grouped by where a rule applies: everything first, then each camera, then the marked areas on it.
class RulesPage extends ConsumerWidget {
  const RulesPage({super.key, this.embedded = false});
  final bool embedded;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Tok.of(context);
    final data = ref.watch(rulesDataProvider);
    final loaded = data.asData?.value;      // what the list below is showing, when there is something to show
    Widget body = data.when(
      loading: () => const Padding(padding: EdgeInsets.all(16), child: SkeletonRows()),
      error: (e, _) => EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load your rules', text: '$e',
          action: OutlinedButton(onPressed: () => ref.invalidate(rulesDataProvider), child: const Text('Try again'))),
      data: (d) => _RulesList(data: d, embedded: embedded),
    );
    if (embedded) {
      return Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Row(children: [
            Expanded(child: Semantics(header: true, child: Text('Your rules', style: display(24)))),
            FilledButton.icon(
              style: FilledButton.styleFrom(minimumSize: const Size(44, 44), padding: const EdgeInsets.symmetric(horizontal: 16), shape: const StadiumBorder()),
              onPressed: () => _open(context, ref, null, loaded),
              icon: const Icon(Icons.add_rounded, size: 20),
              label: const Text('New rule'),
            ),
          ]),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
          child: Align(alignment: Alignment.centerLeft, child: Text('One camera or one marked area per rule, so two queues can have two limits.', style: TextStyle(color: t.fg2, fontSize: 13))),
        ),
        Expanded(child: body),
      ]);
    }
    return Scaffold(
      appBar: AppBar(title: const Text('Your rules')),
      body: SafeArea(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760), child: body))),
    );
  }

  /// Opens the builder for a rule, or a new one. The list it needs (alert kinds, cameras, areas, people) is loaded first,
  /// so a first open from another screen still has something to show.
  static Future<void> _open(BuildContext context, WidgetRef ref, Rule? edit, RulesData? data) async {
    final repo = ref.read(repositoryProvider);
    final loaded = data ?? await ref.read(rulesDataProvider.future);
    if (!context.mounted) return;
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => RuleBuilder(repo: repo, data: loaded, edit: edit)));
    if (saved == true) ref.invalidate(rulesDataProvider);
  }

  static Future<void> openNew(BuildContext context, WidgetRef ref, {CameraItem? camera}) async {
    final repo = ref.read(repositoryProvider);
    final loaded = await ref.read(rulesDataProvider.future);
    if (!context.mounted) return;
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => RuleBuilder(repo: repo, data: loaded, preselected: camera)));
    if (saved == true) ref.invalidate(rulesDataProvider);
  }

  static Future<void> openEdit(BuildContext context, WidgetRef ref, Rule rule, RulesData? data) => _open(context, ref, rule, data);
}

class _RulesList extends ConsumerWidget {
  const _RulesList({required this.data, this.embedded = false});
  final RulesData data;
  final bool embedded;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Tok.of(context);
    if (data.rules.isEmpty) {
      return EmptyState(
        icon: Icons.rule_rounded,
        title: 'No rules of your own yet',
        text: 'The alerts above cover the whole shop. A rule narrows one to a single camera or a marked area, with its own numbers and people.',
        action: FilledButton.icon(onPressed: () => RulesPage._open(context, ref, null, data), icon: const Icon(Icons.add_rounded), label: const Text('Make a rule')),
      );
    }
    // Where first: every camera, then each camera with its areas under it. A rule is read in the place it watches.
    final groups = <String, List<Rule>>{};
    for (final r in data.rules) {
      groups.putIfAbsent(data.placeOf(r), () => []).add(r);
    }
    final keys = groups.keys.toList()..sort((a, b) => a == 'Every camera' ? -1 : (b == 'Every camera' ? 1 : a.compareTo(b)));
    return ListView(
      padding: EdgeInsets.fromLTRB(16, 6, 16, embedded ? navClear : 32),
      children: [
        for (final key in keys) ...[
          GroupLabel(key),
          Group(children: [
            for (final r in groups[key]!)
              RuleRow(rule: r, data: data, onTap: () => RulesPage._open(context, ref, r, data)),
          ]),
        ],
        const SizedBox(height: 10),
        Text('A rule for one camera beats the account-wide setting. A rule for one marked area beats both.',
            style: TextStyle(color: t.fg2, fontSize: 12.5, height: 1.4)),
      ],
    );
  }
}

class RuleRow extends ConsumerWidget {
  const RuleRow({super.key, required this.rule, required this.data, required this.onTap});
  final Rule rule;
  final RulesData data;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Tok.of(context);
    final def = data.defFor(rule.type);
    final when = def?.sentence(rule.condition) ?? rule.description;
    final people = data.people.where((p) => rule.recipientIds.contains(p.id)).length;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: EdgeInsets.fromLTRB(cardPad(context), 12, 10, 12),
        child: Row(children: [
          Badge2(iconForType(rule.type, smart: rule.ai), rule.ai ? Level.ai : Level.ok, ai: rule.ai, size: 42),
          const SizedBox(width: 14),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(rule.name, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5), maxLines: 1, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 2),
              Text(when, style: TextStyle(color: t.fg2, fontSize: 13), maxLines: 2, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 6),
              Wrap(spacing: 6, runSpacing: 4, children: [
                if (rule.isDefault) const Pill('Every camera', Level.mute),
                Pill(people == 0 ? 'No one yet' : '$people ${people == 1 ? 'person' : 'people'}', people == 0 ? Level.warn : Level.mute, icon: Icons.person_outline_rounded),
                if (rule.hasQuietHours) Pill('Quiet ${rule.quietStart}–${rule.quietEnd}', Level.mute, icon: Icons.bedtime_outlined),
              ]),
            ]),
          ),
          Semantics(
            label: '${rule.name} is ${rule.enabled ? 'on' : 'off'}',
            child: Switch(
              value: rule.enabled,
              onChanged: (v) async {
                final repo = ref.read(repositoryProvider);
                try {
                  await repo.updateRule(rule.id, _draftFrom(rule, enabled: v));
                  ref.invalidate(rulesDataProvider);
                } on ApiError catch (e) {
                  if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
                }
              },
            ),
          ),
        ]),
      ),
    );
  }
}

RuleDraft _draftFrom(Rule r, {bool? enabled, Map<String, num>? condition, List<String>? recipientIds, String? quietStart, String? quietEnd, bool clearQuiet = false}) => RuleDraft(
      name: r.name,
      type: r.type,
      cameraId: r.cameraId,
      zoneId: r.zoneId,
      condition: condition ?? r.condition,
      recipientIds: recipientIds ?? r.recipientIds,
      quietStart: clearQuiet ? null : (quietStart ?? r.quietStart),
      quietEnd: clearQuiet ? null : (quietEnd ?? r.quietEnd),
      enabled: enabled ?? r.enabled,
      version: r.version,
    );

/// Four steps: Watch, When, Tell, Review. Same shape everywhere, in the words a shop owner uses.
class RuleBuilder extends ConsumerStatefulWidget {
  const RuleBuilder({super.key, required this.repo, this.data, this.edit, this.preselected});
  final Repository repo;
  final RulesData? data;
  final Rule? edit;
  final CameraItem? preselected;      // "add a rule for this camera" from the camera screen

  @override
  ConsumerState<RuleBuilder> createState() => _RuleBuilderState();
}

class _RuleBuilderState extends ConsumerState<RuleBuilder> {
  static const _steps = ['Watch', 'When', 'Tell', 'Review'];
  int step = 1;
  AlertDef? def;
  String? cameraId;                  // null: every camera
  String? areaId;
  Map<String, num> condition = {};
  final who = <String>{};
  String? quietStart, quietEnd;
  bool saving = false, saved = false, finished = false;
  String? error;
  late final RulesData? data = widget.data;

  @override
  void initState() {
    super.initState();
    final e = widget.edit;
    cameraId = e?.cameraId ?? widget.preselected?.id;
    areaId = e?.zoneId;
    condition = {...?e?.condition};
    who.addAll(e?.recipientIds ?? const []);
    quietStart = e?.quietStart;
    quietEnd = e?.quietEnd;
    def = e == null ? null : data?.defFor(e.type);
    if (def != null) {
      for (final s in def!.settings) {
        condition.putIfAbsent(s.key, () => s.def);
      }
    }
    if (def == null) condition = {};
  }

  bool get _isArea => def?.area == true;
  bool get _areaChosen => !_isArea || areaId != null;
  bool get canContinue => switch (step) {
        1 => def != null && _areaChosen,
        2 => true,
        3 => true,
        _ => true,
      };

  void _pickDef(AlertDef d) {
    setState(() {
      def = d;
      condition = {for (final s in d.settings) s.key: s.def};
      areaId = null;
      if (!d.area && cameraId == null) cameraId = null;
    });
  }

  void _next() {
    if (step < 4) {
      setState(() => step++);
    } else {
      _save();
    }
  }

  void _back() {
    if (step > 1) setState(() => step--);
  }

  Future<void> _save() async {
    if (saving) return;
    setState(() { saving = true; error = null; });
    final d = RuleDraft(
      name: (widget.edit?.name.isNotEmpty ?? false) ? widget.edit!.name : _defaultName(),
      type: def!.type,
      cameraId: _isArea ? cameraId : cameraId, // a camera may be narrowed to one camera without an area
      zoneId: _isArea ? areaId : null,
      condition: condition,
      recipientIds: who.toList(),
      quietStart: quietStart,
      quietEnd: quietEnd,
      enabled: widget.edit?.enabled ?? true,
      version: widget.edit?.version,
    );
    try {
      final savedRule = widget.edit == null ? await widget.repo.createRule(d) : await widget.repo.updateRule(widget.edit!.id, d);
      if (!mounted) return;
      setState(() { saving = false; saved = true; finished = true; });
      // Leave the person on the review step for a moment with what happened, then go back to the list.
      if (savedRule.recipientIds.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Saved. The rule will appear in the app, but no one is messaged until you choose people.')));
        }
      }
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          saving = false;
          error = e.code == 'rule_changed' ? 'Someone else changed this rule while you were editing. Close it and open it again to see the latest.' : e.message;
        });
      }
    }
  }

  Future<void> _test() async {
    final id = widget.edit?.id;
    if (id == null) return;
    try {
      final r = await widget.repo.testRule(id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(r.queued > 0 ? 'Test sent to ${r.sentTo.join(', ')}.' : (r.reason ?? 'No test was sent. Choose someone with a mobile number first.'))));
    } on ApiError catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  String _defaultName() {
    final place = _isArea
        ? (data?.areaFor(areaId)?.cameraName ?? data?.cameraFor(cameraId)?.name ?? '')
        : (data?.cameraFor(cameraId)?.name ?? '');
    return place.isEmpty ? def!.title : '${def!.title} · $place';
  }

  String get _placeWords {
    if (_isArea) {
      final a = data?.areaFor(areaId);
      if (a != null) return '${a.cameraName} · ${areaWord(a.kind)}';
      return 'A marked area';
    }
    return data?.cameraFor(cameraId)?.name ?? 'Every camera';
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final d = def;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.edit == null ? 'New rule' : 'Change rule'),
        actions: [
          if (widget.edit != null && !widget.edit!.isDefault)
            PopupMenuButton<String>(
              tooltip: 'More',
              onSelected: (v) async {
                if (v == 'delete') {
                  try {
                    await widget.repo.deleteRule(widget.edit!.id);
                    if (context.mounted) Navigator.of(context).pop(true);
                  } on ApiError catch (e) {
                    if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
                  }
                }
              },
              itemBuilder: (_) => const [PopupMenuItem(value: 'delete', child: Text('Delete this rule'))],
            ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 760),
            child: Column(children: [
              LinearProgressIndicator(value: step / 4, minHeight: 3, backgroundColor: t.surface2, color: t.brand),
              Expanded(
                child: RiseIn(
                  key: ValueKey(step),
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
                    children: [
                      Text('Step $step of ${_steps.length} · ${_steps[step - 1]}', style: TextStyle(color: t.fg2, fontWeight: FontWeight.w600, fontSize: 13)),
                      const SizedBox(height: 12),
                      ...switch (step) { 1 => _watch(t), 2 => _when(t), 3 => _tell(t), _ => _review(t) },
                      if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))),
                    ],
                  ),
                ),
              ),
              _Bar(
                canBack: step > 1 && !saving,
                onBack: _back,
                last: step == 4,
                canGo: canContinue && !saving && !saved,
                busy: saving,
                onGo: _next,
                summary: d == null ? null : [d.title, _placeWords].join(' · '),
              ),
            ]),
          ),
        ),
      ),
    );
  }

  // ------------------------------------------------------------------ 1. Watch
  List<Widget> _watch(Tok t) {
    final defs = data?.defs ?? const <AlertDef>[];
    final cameras = data?.cameras ?? const <CameraItem>[];
    final areas = data?.areas ?? const <AreaInfo>[];
    Widget choice(AlertDef d, {bool last = false}) => Padding(
          padding: EdgeInsets.only(bottom: last ? 0 : 10),
          child: _Choice(
            icon: iconForType(d.type, smart: d.smart),
            ai: d.smart,
            title: d.title,
            subtitle: d.description,
            selected: def?.type == d.type,
            onTap: () => _pickDef(d),
          ),
        );
    final system = defs.where((d) => !d.smart).toList(), smart = defs.where((d) => d.smart).toList();
    final onCamera = areas.where((a) => a.cameraId == cameraId && a.kind == def?.areaKind).toList();
    return [
      if (widget.edit == null) ...[
        Text('What should we watch?', style: display(22)),
        const SizedBox(height: 12),
        if (system.isNotEmpty) ...[const GroupLabel('System'), for (final d in system) choice(d)],
        if (smart.isNotEmpty) ...[const GroupLabel('Smart alerts'), for (final (i, d) in smart.indexed) choice(d, last: i == smart.length - 1)],
      ] else
        _Chosen(def: def!, subtitle: _placeWords.isEmpty ? (def?.description ?? '') : _placeWords),
      if (def != null) ...[
        const SizedBox(height: 18),
        Text(_isArea ? 'Which camera and marked area?' : 'Where should this apply?', style: display(20)),
        const SizedBox(height: 8),
        if (!_isArea)
          _Choice(icon: Icons.videocam_outlined, title: 'Every camera', subtitle: 'One rule for the whole shop.', selected: cameraId == null, onTap: () => setState(() { cameraId = null; areaId = null; }))
        else
          for (final c in cameras)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                _Choice(icon: Icons.videocam_outlined, title: c.name, subtitle: c.working ? 'Working' : 'Not sending pictures',
                    selected: cameraId == c.id, onTap: () => setState(() { cameraId = c.id; areaId = null; })),
                if (cameraId == c.id)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 0, 2),
                    child: onCamera.isEmpty
                        ? Text('No ${areaWord(def!.areaKind ?? '').toLowerCase()} is drawn on this camera yet. Draw one on the camera first.', style: TextStyle(color: t.warnInk, fontSize: 13))
                        : Column(children: [
                            for (final a in onCamera)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 8),
                                child: _Choice(icon: Icons.crop_free_rounded, title: a.id == areaId ? 'This area' : areaWord(a.kind),
                                    subtitle: a.params.isEmpty ? 'Drawn on ${a.cameraName}' : _areaSentence(a), selected: areaId == a.id, onTap: () => setState(() => areaId = a.id)),
                              ),
                          ]),
                  ),
              ]),
            ),
      ],
    ];
  }

  String _areaSentence(AreaInfo a) {
    final p = a.params;
    return switch (a.kind) {
      'queue_area' => 'More than ${p['length'] ?? 4} waiting for ${p['minutes'] ?? 2} minutes',
      'area_count' => 'More than ${p['limit'] ?? 10} people for ${p['minutes'] ?? 2} minutes',
      _ => 'A phone in use for ${p['seconds'] ?? 30} seconds',
    };
  }

  // ------------------------------------------------------------------ 2. When
  List<Widget> _when(Tok t) {
    final s = def!.settings;
    return [
      Text(s.isEmpty ? 'This one has no numbers to set.' : 'When should we tell you?', style: display(22)),
      const SizedBox(height: 6),
      Text(def!.title, style: TextStyle(color: t.fg2, fontSize: 13.5)),
      const SizedBox(height: 14),
      if (s.isNotEmpty)
        Section(
          title: 'The limit',
          child: Column(children: [
            for (final (i, st) in s.indexed)
              _NumberRow(
                setting: st,
                value: condition[st.key] ?? st.def,
                first: i == 0,
                onChanged: (v) => setState(() => condition[st.key] = v),
              ),
          ]),
        ),
      const SizedBox(height: 14),
      Section(
        title: 'Quiet hours',
        subtitle: 'No messages between them. The alert still appears in the app.',
        trailing: Switch(value: quietStart != null && quietEnd != null, onChanged: (on) => setState(() {
          if (on) { quietStart = '22:00'; quietEnd = '07:00'; } else { quietStart = null; quietEnd = null; }
        })),
        child: (quietStart != null && quietEnd != null)
            ? Row(children: [
                Expanded(child: _Clock(label: 'From', value: quietStart!, onPick: (v) => setState(() => quietStart = v))),
                const SizedBox(width: 10),
                Expanded(child: _Clock(label: 'Until', value: quietEnd!, onPick: (v) => setState(() => quietEnd = v))),
              ])
            : Text('Messages can come at any hour.', style: TextStyle(color: t.fg2, fontSize: 13.5)),
      ),
      if (def!.type == 'crowding' || def!.area)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text('A picture is kept when this happens, so you can see it in the app and in the message.', style: TextStyle(color: t.fg2, fontSize: 12.5, height: 1.4)),
        ),
    ];
  }

  // ------------------------------------------------------------------ 3. Tell
  List<Widget> _tell(Tok t) {
    final people = data?.people ?? const <Person>[];
    return [
      Text('Who hears about it?', style: display(22)),
      const SizedBox(height: 6),
      Text('Everyone you choose gets a WhatsApp message when this rule happens.', style: TextStyle(color: t.fg2, fontSize: 13.5, height: 1.4)),
      const SizedBox(height: 14),
      if (people.isEmpty)
        EmptyState(icon: Icons.people_outline_rounded, title: 'No one on your list yet', text: 'Add the people you want us to message in Settings, then come back.', action: OutlinedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Back to settings')))
      else
        Group(children: [
          for (final p in people)
            Semantics(
              button: true,
              checked: who.contains(p.id),
              label: p.name,
              child: InkWell(
                onTap: () => setState(() { who.contains(p.id) ? who.remove(p.id) : who.add(p.id); }),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                  child: Row(children: [
                    Icon(who.contains(p.id) ? Icons.check_circle_rounded : Icons.radio_button_unchecked_rounded,
                        color: who.contains(p.id) ? t.brand : t.fg2, size: 24),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                        const SizedBox(height: 2),
                        Text(p.hasPhone ? p.phone : 'No mobile number yet', style: TextStyle(color: p.hasPhone ? t.fg2 : t.warnInk, fontSize: 12.5)),
                      ]),
                    ),
                  ]),
                ),
              ),
            ),
        ]),
      const SizedBox(height: 10),
      Text(who.isEmpty
          ? 'With no one chosen, the rule only appears in the app.'
          : '${who.length} ${who.length == 1 ? 'person' : 'people'} will be messaged.',
          style: TextStyle(color: who.isEmpty ? t.warnInk : t.fg2, fontSize: 12.5)),
    ];
  }

  // ------------------------------------------------------------------ 4. Review
  List<Widget> _review(Tok t) {
    final def = this.def!;
    final people = data?.people ?? const <Person>[];
    final chosen = people.where((p) => who.contains(p.id)).map((p) => p.name).toList();
    return [
      Text('Check it once', style: display(22)),
      const SizedBox(height: 12),
      Section(
        title: def.title,
        icon: iconForType(def.type, smart: def.smart),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(def.sentence(condition), style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w600, height: 1.35)),
          const SizedBox(height: 10),
          _Line(icon: Icons.place_outlined, text: _placeWords),
          _Line(icon: Icons.people_outline_rounded, text: chosen.isEmpty ? 'No one yet: it only appears in the app' : chosen.join(', ')),
          _Line(icon: Icons.bedtime_outlined, text: quietStart == null || quietEnd == null ? 'Messages can come at any hour' : 'Quiet from $quietStart to $quietEnd'),
        ]),
      ),
      const SizedBox(height: 12),
      if (widget.edit != null && !widget.edit!.isDefault)
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(minimumSize: const Size(44, 48)),
          onPressed: _test,
          icon: const Icon(Icons.send_outlined, size: 18),
          label: const Text('Send a test message'),
        ),
      if (widget.edit != null) _Deliveries(ruleId: widget.edit!.id, repo: widget.repo),
      const SizedBox(height: 8),
      Text('Saving replaces the old setting for this place. Other rules are not changed.', style: TextStyle(color: t.fg2, fontSize: 12.5, height: 1.4)),
    ];
  }
}

class _Deliveries extends StatelessWidget {
  const _Deliveries({required this.ruleId, required this.repo});
  final String ruleId;
  final Repository repo;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder(
      future: repo.ruleDeliveries(ruleId),
      builder: (context, snap) {
        final rows = snap.data ?? const <RuleDelivery>[];
        if (rows.isEmpty) return const SizedBox.shrink();
        String when(DateTime? d) => d == null ? '' : friendlyWhen(d.toIso8601String());
        final days = rows.where((r) => !r.sent).toList();
        return Padding(
          padding: const EdgeInsets.only(top: 14),
          child: Section(
            title: 'Recent messages',
            subtitle: days.isEmpty ? 'Every message sent so far went through.' : 'A message that did not go through says why.',
            child: Column(children: [
              for (final r in rows.take(4))
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(children: [
                    Icon(r.sent ? Icons.check_circle_outline_rounded : Icons.error_outline_rounded, size: 18, color: r.sent ? t.okInk : t.crit),
                    const SizedBox(width: 8),
                    Expanded(child: Text([r.person ?? 'Someone', if (!r.sent && r.error != null) r.error!, when(r.at)].where((x) => x.isNotEmpty).join(' · '), style: TextStyle(color: t.fg2, fontSize: 12.5))),
                  ]),
                ),
            ]),
          ),
        );
      },
    );
  }
}

class _Chosen extends StatelessWidget {
  const _Chosen({required this.def, required this.subtitle});
  final AlertDef def;
  final String subtitle;
  @override
  Widget build(BuildContext context) => _Choice(icon: iconForType(def.type, smart: def.smart), ai: def.smart, title: def.title, subtitle: subtitle, selected: true, onTap: null);
}

class _Choice extends StatelessWidget {
  const _Choice({required this.icon, required this.title, required this.subtitle, required this.selected, this.onTap, this.ai = false});
  final IconData icon;
  final String title, subtitle;
  final bool selected, ai;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Pressable(
      onTap: onTap,
      selected: selected,
      label: title,
      child: AnimatedContainer(
        duration: reduceMotion(context) ? Duration.zero : Motion.quick,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: selected ? (ai ? t.aiSoft : t.brandSoft) : t.surface,
          borderRadius: BorderRadius.circular(Radii.tile),
          border: Border.all(color: selected ? (ai ? t.ai : t.brand) : t.line, width: selected ? 1.5 : 1),
        ),
        child: Row(children: [
          Badge2(icon, ai ? Level.ai : Level.ok, ai: ai, size: 38),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
            if (subtitle.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 2), child: Text(subtitle, style: TextStyle(color: t.fg2, fontSize: 12.5, height: 1.35), maxLines: 2, overflow: TextOverflow.ellipsis)),
          ])),
          if (selected) Icon(Icons.check_circle_rounded, color: ai ? t.ai : t.brand, size: 22) else const SizedBox(width: 22),
        ]),
      ),
    );
  }
}

class _NumberRow extends StatelessWidget {
  const _NumberRow({required this.setting, required this.value, required this.onChanged, this.first = false});
  final Setting setting;
  final num value;
  final ValueChanged<num> onChanged;
  final bool first;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final n = value.round();
    Widget button(IconData icon, int delta, String label, bool enabled) => IconButton(
          onPressed: enabled ? () => onChanged((n + delta).clamp(setting.min, setting.max)) : null,
          tooltip: label,
          icon: Icon(icon, size: 20),
          style: IconButton.styleFrom(minimumSize: const Size(44, 44), backgroundColor: t.surface2, foregroundColor: t.fg, disabledBackgroundColor: t.surface2.withValues(alpha: .5)),
        );
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(border: first ? null : Border(top: BorderSide(color: t.line))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(setting.label, style: TextStyle(color: t.fg2, fontSize: 12.5, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        Row(children: [
          Expanded(child: Text('$n ${setting.unit}', style: display(22))),
          button(Icons.remove_rounded, -1, 'Less', n > setting.min),
          const SizedBox(width: 8),
          button(Icons.add_rounded, 1, 'More', n < setting.max),
        ]),
      ]),
    );
  }
}

class _Clock extends StatelessWidget {
  const _Clock({required this.label, required this.value, required this.onPick});
  final String label, value;
  final ValueChanged<String> onPick;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final parts = value.split(':');
    final shown = TimeOfDay(hour: int.tryParse(parts.first) ?? 22, minute: int.tryParse(parts.length > 1 ? parts[1] : '0') ?? 0);
    return InkWell(
      borderRadius: BorderRadius.circular(Radii.control),
      onTap: () async {
        final picked = await showTimePicker(context: context, initialTime: shown, helpText: '$label quiet hours');
        if (picked != null) onPick('${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}');
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(Radii.control)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: TextStyle(color: t.fg2, fontSize: 12, fontWeight: FontWeight.w600)),
          const SizedBox(height: 2),
          Text(shown.format(context), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
        ]),
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(icon, size: 17, color: t.fg2),
        const SizedBox(width: 8),
        Expanded(child: Text(text, style: TextStyle(color: t.fg2, fontSize: 13.5, height: 1.35))),
      ]),
    );
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.canBack, required this.onBack, required this.last, required this.canGo, required this.busy, required this.onGo, this.summary});
  final bool canBack, last, canGo, busy;
  final VoidCallback onBack, onGo;
  final String? summary;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      padding: EdgeInsets.fromLTRB(16, 10, 16, MediaQuery.paddingOf(context).bottom + 10),
      decoration: BoxDecoration(color: t.surface, border: Border(top: BorderSide(color: t.line))),
      child: Row(children: [
        if (canBack) OutlinedButton(onPressed: onBack, style: OutlinedButton.styleFrom(minimumSize: const Size(84, 50)), child: const Text('Back')),
        if (summary != null && canBack) const SizedBox(width: 10),
        Expanded(child: summary == null ? const SizedBox() : Text(summary!, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: t.fg2, fontSize: 12.5))),
        FilledButton(
          onPressed: canGo ? onGo : null,
          style: FilledButton.styleFrom(minimumSize: const Size(120, 50), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.button))),
          child: busy
              ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white))
              : Text(last ? 'Save rule' : 'Continue'),
        ),
      ]),
    );
  }
}
