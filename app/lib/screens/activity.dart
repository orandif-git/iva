import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';
import 'vehicles.dart';

/// The kinds of alert that belong to each part of Activity. A part is shown only when the customer has something set up for it.
const _peopleAlerts = {'crowding'};
const _vehicleAlerts = {'blocked_plate', 'vehicle_overstay', 'low_confidence_plate'};
const _areaAlerts = {'people_in_area', 'queue_long', 'phone_use'};
const _healthAlerts = {'camera_offline', 'recorder_offline', 'connection_unstable', 'back_to_normal'};

enum Section {
  people('People', Icons.groups_outlined),
  vehicles('Vehicles', Icons.directions_car_outlined),
  areas('Areas', Icons.crop_free_rounded),
  health('Health', Icons.monitor_heart_outlined);

  const Section(this.label, this.icon);
  final String label;
  final IconData icon;
}

class _Plan {
  const _Plan(this.sections, this.defs, this.cameras);
  final List<Section> sections;
  final List<AlertDef> defs;
  final List<CameraItem> cameras;
}

/// Activity: what your cameras have seen, one part for each thing the customer has set up (people counting, number plates, marked areas, equipment health),
/// over Today, Yesterday, 7 days or 30 days. Number plates and the blocked list live here (Vehicles); what happened that needs you is in Alerts (the bell).
class ActivityPage extends StatefulWidget {
  const ActivityPage({super.key, required this.repo, this.refresh});
  final Repository repo;
  final Listenable? refresh;
  @override
  State<ActivityPage> createState() => _ActivityPageState();
}

class _ActivityPageState extends State<ActivityPage> {
  Period period = Period.today;
  Section? chosen;
  late Future<_Plan> plan = _load();

  @override
  void initState() {
    super.initState();
    widget.refresh?.addListener(_reload);
  }

  @override
  void dispose() {
    widget.refresh?.removeListener(_reload);
    super.dispose();
  }

  void _reload() {
    if (mounted) setState(() { plan = _load(); });
  }

  Future<_Plan> _load() async {
    final r = await Future.wait([widget.repo.alertDefs(), widget.repo.cameras()]);
    final defs = r[0] as List<AlertDef>, cams = r[1] as List<CameraItem>;
    bool on(Set<String> kinds) => defs.any((d) => d.enabled && kinds.contains(d.type));
    return _Plan([
      if (cams.any((c) => c.useFootfall) || on(_peopleAlerts)) Section.people,
      if (cams.any((c) => c.useAnpr) || on(_vehicleAlerts)) Section.vehicles,
      if (on(_areaAlerts)) Section.areas,
      if (on(_healthAlerts) || cams.isNotEmpty) Section.health,
    ], defs, cams);
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<_Plan>(
      future: plan,
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load Activity', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}', action: OutlinedButton(onPressed: _reload, child: const Text('Try again')));
        if (!snap.hasData) return const Loading(text: 'Loading Activity');
        final p = snap.data!;
        if (p.sections.isEmpty) {
          return const EmptyState(icon: Icons.insights_outlined, title: 'Nothing to show yet', text: 'Add a camera, and turn on counting people, reading number plates or an alert in AI Watch. What they see appears here.');
        }
        final section = p.sections.contains(chosen) ? chosen! : p.sections.first;
        return Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
            child: Column(children: [
              if (p.sections.length > 1)
                SizedBox(
                  width: double.infinity,
                  child: SegmentedButton<Section>(
                    showSelectedIcon: false,
                    segments: [for (final s in p.sections) ButtonSegment(value: s, label: Text(s.label, maxLines: 1, overflow: TextOverflow.ellipsis))],
                    selected: {section},
                    onSelectionChanged: (v) => setState(() => chosen = v.first),
                  ),
                ),
              const SizedBox(height: 8),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(children: [
                  for (final x in Period.values)
                    Padding(padding: const EdgeInsets.only(right: 6), child: ChoiceChip(label: Text(x.label), labelPadding: const EdgeInsets.symmetric(horizontal: 2), visualDensity: VisualDensity.compact, selected: period == x, showCheckmark: false, onSelected: (_) => setState(() => period = x))),
                ]),
              ),
            ]),
          ),
          Divider(height: 12, color: t.line.withValues(alpha: 0)),
          Expanded(
            child: switch (section) {
              Section.people => PeopleSection(key: ValueKey('people${period.name}'), repo: widget.repo, period: period, cameras: p.cameras.where((c) => c.useFootfall).toList()),
              Section.vehicles => VehiclesTab(key: ValueKey('vehicles${period.name}'), repo: widget.repo, period: period),
              Section.areas => AreasSection(key: ValueKey('areas${period.name}'), repo: widget.repo, period: period, defs: p.defs.where((d) => d.enabled && _areaAlerts.contains(d.type)).toList()),
              Section.health => HealthSection(key: ValueKey('health${period.name}'), repo: widget.repo, period: period, defs: p.defs),
            },
          ),
        ]);
      },
    );
  }
}

// ----------------------------------------------------------------------------------------------------------------------------- people
class PeopleSection extends StatefulWidget {
  const PeopleSection({super.key, required this.repo, required this.period, required this.cameras});
  final Repository repo;
  final Period period;
  final List<CameraItem> cameras;
  @override
  State<PeopleSection> createState() => _PeopleSectionState();
}

class _PeopleSectionState extends State<PeopleSection> {
  String? cameraId;
  late Future<FootfallRange> data = _load();

  Future<FootfallRange> _load() => widget.repo.footfallRange(period: widget.period, cameraId: cameraId);

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final days = !widget.period.hourly;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        Widget chip(String label, bool on, VoidCallback tap) => Padding(padding: const EdgeInsets.only(right: 8), child: ChoiceChip(label: Text(label), selected: on, onSelected: (_) => tap(), showCheckmark: false));
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
          if (widget.cameras.length > 1)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(children: [
                  chip('All cameras', cameraId == null, () => setState(() { cameraId = null; data = _load(); })),
                  for (final c in widget.cameras) chip(c.name, cameraId == c.id, () => setState(() { cameraId = c.id; data = _load(); })),
                ]),
              ),
            ),
          if (snap.hasError)
            EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the counts', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}', action: OutlinedButton(onPressed: () => setState(() { data = _load(); }), child: const Text('Try again')))
          else if (!snap.hasData)
            const Padding(padding: EdgeInsets.all(40), child: Loading())
          else ...[
            _Kpis(snap.data!),
            const GroupLabel('Over time'),
            Group(padding: EdgeInsets.all(cardPad(context)), children: [
              Row(children: [Expanded(child: Text(days ? 'By day' : 'By hour', style: TextStyle(color: t.fg2))), _Dot(t.brand, 'In'), const SizedBox(width: 14), _Dot(t.fg2.withValues(alpha: .55), 'Out')]),
              const SizedBox(height: 12),
              snap.data!.bars.any((b) => b.peopleIn > 0 || b.peopleOut > 0)
                  ? SizedBox(height: 190, child: CustomPaint(size: Size.infinite, painter: _BarsPainter(snap.data!.bars, t)))
                  : Padding(padding: const EdgeInsets.symmetric(vertical: 24), child: Center(child: Text('No one was counted in this period.', style: TextStyle(color: t.fg2)))),
            ]),
          ],
        ]);
      },
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot(this.color, this.label);
  final Color color;
  final String label;
  @override
  Widget build(BuildContext context) => Row(children: [Container(width: 10, height: 10, decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(3))), const SizedBox(width: 6), Text(label, style: TextStyle(color: Tok.of(context).fg2, fontSize: 13))]);
}

class _Kpis extends StatelessWidget {
  const _Kpis(this.r);
  final FootfallRange r;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget card(String label, String value) => Expanded(
          child: Container(
            padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
            decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: t.line)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label, style: TextStyle(color: t.fg2, fontSize: 12.5), maxLines: 1, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 4),
              FittedBox(fit: BoxFit.scaleDown, alignment: Alignment.centerLeft, child: Text(value, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700, letterSpacing: -.5))),
            ]),
          ),
        );
    return Column(children: [
      Row(children: [card('People in', '${r.peopleIn}'), const SizedBox(width: 10), card('People out', '${r.peopleOut}')]),
      const SizedBox(height: 10),
      Row(children: [card('Net', '${r.net >= 0 ? '+' : ''}${r.net}'), const SizedBox(width: 10), card('Busiest hour', r.peakLabel ?? '–')]),
    ]);
  }
}

class _BarsPainter extends CustomPainter {
  _BarsPainter(this.bars, this.t);
  final List<FootfallBar> bars;
  final Tok t;

  @override
  void paint(Canvas canvas, Size size) {
    if (bars.isEmpty) return;
    final top = bars.fold<int>(1, (m, b) => [m, b.peopleIn, b.peopleOut].reduce((a, c) => a > c ? a : c));
    const labelH = 22.0;
    final h = size.height - labelH, slot = size.width / bars.length, bw = (slot * 0.34).clamp(2.0, 18.0);
    final grid = Paint()..color = t.line..strokeWidth = 1;
    for (var i = 0; i <= 3; i++) {
      final y = h * i / 3;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    final inP = Paint()..color = t.brand, outP = Paint()..color = t.fg2.withValues(alpha: .55);
    final every = (bars.length / 6).ceil().clamp(1, 99);
    for (var i = 0; i < bars.length; i++) {
      final cx = slot * i + slot / 2, b = bars[i];
      final hi = h * b.peopleIn / top, ho = h * b.peopleOut / top;
      canvas.drawRRect(RRect.fromRectAndRadius(Rect.fromLTWH(cx - bw - 1, h - hi, bw, hi), const Radius.circular(3)), inP);
      canvas.drawRRect(RRect.fromRectAndRadius(Rect.fromLTWH(cx + 1, h - ho, bw, ho), const Radius.circular(3)), outP);
      if (i % every == 0) {
        final tp = TextPainter(text: TextSpan(text: b.label, style: TextStyle(color: t.fg2, fontSize: 10.5)), textDirection: TextDirection.ltr)..layout(maxWidth: slot * every);
        tp.paint(canvas, Offset((cx - tp.width / 2).clamp(0.0, size.width - tp.width), h + 6));
      }
    }
  }

  @override
  bool shouldRepaint(_BarsPainter o) => o.bars != bars;
}

// ------------------------------------------------------------------------------------------------------------------------------ areas
String _ago(DateTime? d) {
  if (d == null) return '';
  final s = DateTime.now().difference(d).inSeconds.clamp(0, 1 << 30);
  return shortAgo(s);
}

/// The marked areas the customer watches (people in an area, a queue, phone use): how often each fired in the period, and where.
class AreasSection extends StatelessWidget {
  const AreasSection({super.key, required this.repo, required this.period, required this.defs});
  final Repository repo;
  final Period period;
  final List<AlertDef> defs;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<List<AlertCount>>(
      future: repo.alertCounts(period),
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load this', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}');
        if (!snap.hasData) return const Padding(padding: EdgeInsets.all(40), child: Loading());
        final counts = snap.data!;
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
          for (final d in defs) ...[
            () {
              final mine = counts.where((c) => c.rule == d.type).toList()..sort((a, b) => b.count.compareTo(a.count));
              final total = mine.fold<int>(0, (s, c) => s + c.count);
              final last = mine.map((c) => c.last).whereType<DateTime>().fold<DateTime?>(null, (m, x) => m == null || x.isAfter(m) ? x : m);
              return Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Group(padding: EdgeInsets.all(cardPad(context)), children: [
                  Row(children: [
                    Badge2(iconForType(d.type), Level.ai, ai: true, size: 42),
                    const SizedBox(width: 14),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(d.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                      Text(d.summary, style: TextStyle(color: t.fg2, fontSize: 13)),
                    ])),
                    Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                      Text('$total', style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700, letterSpacing: -.5)),
                      Text(total == 1 ? 'alert' : 'alerts', style: TextStyle(color: t.fg2, fontSize: 12)),
                    ]),
                  ]),
                  if (mine.isEmpty)
                    Padding(padding: const EdgeInsets.only(top: 12), child: Text('Nothing to report in this period.', style: TextStyle(color: t.fg2)))
                  else ...[
                    const SizedBox(height: 12),
                    for (final c in mine.take(5))
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 5),
                        child: Row(children: [
                          Expanded(flex: 3, child: Text(c.place.isEmpty ? 'Not named' : c.place, maxLines: 1, overflow: TextOverflow.ellipsis)),
                          Expanded(flex: 4, child: ClipRRect(borderRadius: BorderRadius.circular(4), child: LinearProgressIndicator(value: c.count / mine.first.count, minHeight: 8, backgroundColor: t.brandSoft, color: t.ai))),
                          SizedBox(width: 40, child: Text('${c.count}', textAlign: TextAlign.right, style: const TextStyle(fontWeight: FontWeight.w700))),
                        ]),
                      ),
                    if (last != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text('Last one ${_ago(last)} ago', style: TextStyle(color: t.fg2, fontSize: 12.5))),
                  ],
                ]),
              );
            }(),
          ],
          if (defs.isEmpty) const EmptyState(icon: Icons.crop_free_rounded, title: 'No area alerts are on', text: 'Mark an area on a camera in AI Watch, for example a queue, and its activity appears here.'),
        ]);
      },
    );
  }
}

// ----------------------------------------------------------------------------------------------------------------------------- health
/// Cameras and recorders: how many are working now, and how often something went offline in the period.
class HealthSection extends StatelessWidget {
  const HealthSection({super.key, required this.repo, required this.period, required this.defs});
  final Repository repo;
  final Period period;
  final List<AlertDef> defs;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder<(Health, List<AlertCount>)>(
      future: Future.wait([repo.health(), repo.alertCounts(period)]).then((r) => (r[0] as Health, r[1] as List<AlertCount>)),
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load this', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}');
        if (!snap.hasData) return const Padding(padding: EdgeInsets.all(40), child: Loading());
        final (h, counts) = snap.data!;
        final incidents = counts.where((c) => _healthAlerts.contains(c.rule) && c.rule != 'back_to_normal').toList()..sort((a, b) => b.count.compareTo(a.count));
        String title(String rule) => defs.where((d) => d.type == rule).firstOrNull?.title ?? rule.replaceAll('_', ' ');
        Widget card(String label, String value, {bool bad = false}) => Expanded(
              child: Container(
                padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
                decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: t.line)),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: TextStyle(color: t.fg2, fontSize: 12.5), maxLines: 1, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text(value, style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700, letterSpacing: -.5, color: bad ? t.crit : t.fg)),
                ]),
              ),
            );
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
          Row(children: [
            card('Cameras working', '${h.camerasWorking} of ${h.camerasTotal}', bad: h.camerasWorking < h.camerasTotal),
            const SizedBox(width: 10),
            card('Recorders online', '${h.recordersOnline} of ${h.recordersTotal}', bad: h.recordersOnline < h.recordersTotal),
          ]),
          GroupLabel('Went offline · ${period.label.toLowerCase()}'),
          if (incidents.isEmpty)
            Group(children: [Padding(padding: EdgeInsets.all(cardPad(context)), child: Row(children: [Icon(Icons.check_circle_rounded, color: t.okInk), const SizedBox(width: 12), const Expanded(child: Text('Nothing went offline in this period.', style: TextStyle(fontWeight: FontWeight.w600)))]))])
          else
            Group(children: [
              for (final c in incidents)
                Padding(
                  padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
                  child: Row(children: [
                    Badge2(c.rule == 'recorder_offline' ? Icons.dns_outlined : (c.rule == 'connection_unstable' ? Icons.wifi_off_rounded : Icons.videocam_off_outlined), Level.bad, size: 40),
                    const SizedBox(width: 14),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(c.place.isEmpty ? title(c.rule) : c.place, style: const TextStyle(fontWeight: FontWeight.w700)),
                      Text([title(c.rule), if (c.last != null) 'last ${_ago(c.last)} ago'].join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
                    ])),
                    Text('${c.count}×', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                  ]),
                ),
            ]),
        ]);
      },
    );
  }
}
