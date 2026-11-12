import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import 'common.dart';
import 'recorders.dart' show RecorderHealthCard;
import 'vehicles.dart';
import '../widgets/motion.dart';

/// The kinds of alert that belong to each part of Activity. A part is shown only when the customer has something set up for it.
const _peopleAlerts = {'crowding'};
const _vehicleAlerts = {'blocked_plate', 'vehicle_overstay', 'low_confidence_plate'};
const _areaAlerts = {'people_in_area', 'queue_long', 'phone_use'};
const _healthAlerts = {'camera_offline', 'recorder_offline', 'connection_unstable', 'back_to_normal', 'disk_fault', 'disk_wear', 'recording_stopped', 'clock_wrong', 'recorder_restarted', 'hardware_fault', 'storage_full', 'view_changed', 'picture_poor'};

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
  String? cameraId;            // narrows People and Vehicles to one camera
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
        if (!snap.hasData) return const SkeletonPage(label: 'Loading Activity');
        final p = snap.data!;
        if (p.sections.isEmpty) {
          return const EmptyState(icon: Icons.insights_outlined, title: 'Nothing to show yet', text: 'Add a camera, and turn on counting people, reading number plates or an alert in AI Watch. What they see appears here.');
        }
        final section = p.sections.contains(chosen) ? chosen! : p.sections.first;
        // Which cameras can the second filter offer for this part?
        final cams = switch (section) {
          Section.people => p.cameras.where((c) => c.useFootfall).toList(),
          Section.vehicles => p.cameras.where((c) => c.useAnpr).toList(),
          _ => const <CameraItem>[],
        };
        final camera = cams.where((c) => c.id == cameraId).firstOrNull;
        return Column(children: [
          // One quiet header: the parts as a segmented control, then the two filters side by side, then the exact dates.
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (MediaQuery.sizeOf(context).width < 900) const Padding(padding: EdgeInsets.only(bottom: 14), child: PageTitle('Activity')),
              if (p.sections.length > 1)
                SizedBox(
                  width: double.infinity,
                  child: Segments<Section>(
                    items: [for (final s in p.sections) (s, s.label)],
                    value: section,
                    onChanged: (v) { HapticFeedback.selectionClick(); setState(() { chosen = v; cameraId = null; }); },
                  ),
                ),
              const SizedBox(height: 12),
              Row(children: [
                FilterMenu<Period>(
                  icon: Icons.calendar_today_rounded,
                  label: period.label,
                  value: period,
                  items: [for (final x in Period.values) (x, x.label)],
                  onSelected: (x) => setState(() => period = x),
                ),
                if (cams.length > 1) ...[
                  const SizedBox(width: 8),
                  Flexible(
                    child: FilterMenu<String?>(
                      icon: Icons.videocam_outlined,
                      label: camera?.name ?? 'All cameras',
                      value: camera?.id,
                      items: [(null, 'All cameras'), for (final c in cams) (c.id, c.name)],
                      onSelected: (x) => setState(() => cameraId = x),
                    ),
                  ),
                ],
              ]),
              Padding(padding: const EdgeInsets.fromLTRB(4, 8, 4, 4), child: Text(periodRange(period), style: TextStyle(color: t.fg2, fontSize: 13))),
            ]),
          ),
          Expanded(
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 180),
              child: KeyedSubtree(
                key: ValueKey('${section.name}${period.name}${camera?.id}'),
                child: switch (section) {
                  Section.people => PeopleSection(repo: widget.repo, period: period, cameraId: camera?.id),
                  Section.vehicles => VehiclesTab(repo: widget.repo, period: period, cameraId: camera?.id),
                  Section.areas => AreasSection(repo: widget.repo, period: period, defs: p.defs.where((d) => d.enabled && _areaAlerts.contains(d.type)).toList()),
                  Section.health => HealthSection(repo: widget.repo, period: period, defs: p.defs),
                },
              ),
            ),
          ),
        ]);
      },
    );
  }
}

/// "Wed 11 Nov" for a day, "5 Nov – 11 Nov" for a stretch: exactly which dates the numbers cover (days are cut at midnight in India).
String periodRange(Period p) {
  const wd = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final ist = DateTime.now().toUtc().add(const Duration(hours: 5, minutes: 30));
  final today = DateTime.utc(ist.year, ist.month, ist.day);
  final from = today.add(Duration(days: p.fromDay)), last = today.add(Duration(days: p.toDay - 1));
  String d(DateTime x) => '${x.day} ${mo[x.month - 1]}';
  return from == last ? '${wd[from.weekday - 1]} ${d(from)}' : '${d(from)} – ${d(last)}';
}

/// A pull-down filter, the way iPhone apps do it: a quiet capsule showing the current choice, and a menu with a tick on it. Used for the period and the camera.
class FilterMenu<T> extends StatelessWidget {
  const FilterMenu({super.key, required this.icon, required this.label, required this.value, required this.items, required this.onSelected});
  final IconData icon;
  final String label;
  final T value;
  final List<(T, String)> items;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return PopupMenuButton<T>(
      tooltip: label,
      position: PopupMenuPosition.under,
      offset: const Offset(0, 6),
      initialValue: value,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.button)),
      color: t.surface,
      elevation: 8,
      onOpened: HapticFeedback.selectionClick,
      onSelected: (v) { HapticFeedback.selectionClick(); onSelected(v); },
      itemBuilder: (_) => [
        for (final (v, text) in items)
          PopupMenuItem<T>(
            value: v,
            height: 44,
            child: Row(children: [
              Expanded(child: Text(text, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontWeight: v == value ? FontWeight.w700 : FontWeight.w500))),
              if (v == value) Icon(Icons.check_rounded, size: 20, color: t.brand),
            ]),
          ),
      ],
      child: Container(
        height: 38,
        padding: const EdgeInsets.fromLTRB(12, 0, 8, 0),
        decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.tile), border: Border.all(color: t.line)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 16, color: t.brand),
          const SizedBox(width: 8),
          Flexible(child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14))),
          const SizedBox(width: 2),
          Icon(Icons.keyboard_arrow_down_rounded, size: 20, color: t.fg2),
        ]),
      ),
    );
  }
}

// ----------------------------------------------------------------------------------------------------------------------------- people
class PeopleSection extends StatefulWidget {
  const PeopleSection({super.key, required this.repo, required this.period, this.cameraId});
  final Repository repo;
  final Period period;
  final String? cameraId;
  @override
  State<PeopleSection> createState() => _PeopleSectionState();
}

class _PeopleSectionState extends State<PeopleSection> {
  late Future<FootfallRange> data = widget.repo.footfallRange(period: widget.period, cameraId: widget.cameraId);

  void _retry() => setState(() { data = widget.repo.footfallRange(period: widget.period, cameraId: widget.cameraId); });

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final byDay = !widget.period.hourly;
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
          if (snap.hasError)
            EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the counts', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}', action: OutlinedButton(onPressed: _retry, child: const Text('Try again')))
          else if (!snap.hasData)
            const SkeletonRows()
          else ...[
            _StatsCard(snap.data!),
            const GroupLabel('Over time'),
            Group(padding: EdgeInsets.all(cardPad(context)), children: [
              Row(children: [Expanded(child: Text(byDay ? 'People in and out, by day' : 'People in and out, by hour', style: TextStyle(color: t.fg2, fontSize: 13.5))), _Dot(t.brand, 'In'), const SizedBox(width: 14), _Dot(t.fg2.withValues(alpha: .55), 'Out')]),
              const SizedBox(height: 20),
              snap.data!.bars.any((b) => b.peopleIn > 0 || b.peopleOut > 0)
                  ? SizedBox(height: 190, child: Grow(key: ValueKey(snap.data), builder: (_, v) => CustomPaint(size: Size.infinite, painter: _BarsPainter(snap.data!.bars, t, v))))
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

/// The four numbers as one grouped card with hairlines between them, instead of four separate boxes.
class _StatsCard extends StatelessWidget {
  const _StatsCard(this.r);
  final FootfallRange r;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    Widget stat(String label, String value, IconData icon) => Expanded(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [Icon(icon, size: 15, color: t.fg2), const SizedBox(width: 6), Flexible(child: Text(label, style: TextStyle(color: t.fg2, fontSize: 13), maxLines: 1, overflow: TextOverflow.ellipsis))]),
              const SizedBox(height: 6),
              FittedBox(fit: BoxFit.scaleDown, alignment: Alignment.centerLeft, child: Text(value, style: display(28))),
            ]),
          ),
        );
    final hair = Container(width: 1, color: t.line);
    return Container(
      decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.tile), border: Border.all(color: t.line)),
      child: Column(children: [
        IntrinsicHeight(child: Row(children: [stat('People in', '${r.peopleIn}', Icons.south_west_rounded), hair, stat('People out', '${r.peopleOut}', Icons.north_east_rounded)])),
        Divider(height: 1, color: t.line),
        IntrinsicHeight(child: Row(children: [stat('Net', '${r.net >= 0 ? '+' : ''}${r.net}', Icons.swap_vert_rounded), hair, stat('Busiest hour', r.peakLabel ?? '–', Icons.schedule_rounded)])),
      ]),
    );
  }
}

class _BarsPainter extends CustomPainter {
  _BarsPainter(this.bars, this.t, [this.grow = 1]);
  final List<FootfallBar> bars;
  final Tok t;
  final double grow;     // 0 to 1: the bars rise from the floor when the chart appears

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
      final rise = Curves.easeOut.transform(((grow * (1 + bars.length * .035)) - i * .035).clamp(0.0, 1.0));     // one after another, left to right
      final hi = h * b.peopleIn / top * rise, ho = h * b.peopleOut / top * rise;
      canvas.drawRRect(RRect.fromRectAndRadius(Rect.fromLTWH(cx - bw - 1, h - hi, bw, hi), const Radius.circular(5)), inP);
      canvas.drawRRect(RRect.fromRectAndRadius(Rect.fromLTWH(cx + 1, h - ho, bw, ho), const Radius.circular(5)), outP);
      if (i % every == 0) {
        final tp = TextPainter(text: TextSpan(text: b.label, style: TextStyle(color: t.fg2, fontSize: 10.5)), textDirection: TextDirection.ltr)..layout(maxWidth: slot * every);
        tp.paint(canvas, Offset((cx - tp.width / 2).clamp(0.0, size.width - tp.width), h + 6));
      }
    }
  }

  @override
  bool shouldRepaint(_BarsPainter o) => o.bars != bars || o.grow != grow;
}

// ------------------------------------------------------------------------------------------------------------------------------ areas
String _ago(DateTime? d) {
  if (d == null) return '';
  final s = DateTime.now().difference(d).inSeconds.clamp(0, 1 << 30);
  return shortAgo(s);
}

/// Where it happened, without repeating the alert's own name in front ("Too many people in an area · Jas Office" reads as "Jas Office").
String _placeName(String place, String title) {
  var p = place.trim();
  if (p.toLowerCase().startsWith(title.toLowerCase())) p = p.substring(title.length).replaceFirst(RegExp(r'^[\s·:,-]+'), '');
  return p.isEmpty ? 'Not named' : p;
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
        if (!snap.hasData) return const SkeletonRows();
        final counts = snap.data!;
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, navClear), children: [
          for (final d in defs) ...[
            () {
              final mine = counts.where((c) => c.rule == d.type).toList()..sort((a, b) => b.count.compareTo(a.count));
              final total = mine.fold<int>(0, (s, c) => s + c.count);
              final last = mine.map((c) => c.last).whereType<DateTime>().fold<DateTime?>(null, (m, x) => m == null || x.isAfter(m) ? x : m);
              return Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Container(
                  padding: EdgeInsets.all(cardPad(context)),
                  decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Row(children: [
                    Badge2(iconForType(d.type), Level.ai, ai: true, size: 42),
                    const SizedBox(width: 14),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(d.title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                      Text(d.summary, style: TextStyle(color: t.fg2, fontSize: 13)),
                    ])),
                    Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                      Text('$total', style: display(26)),
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
                          Expanded(flex: 3, child: Text(_placeName(c.place, d.title), maxLines: 1, overflow: TextOverflow.ellipsis)),
                          Expanded(flex: 4, child: ClipRRect(borderRadius: BorderRadius.circular(4), child: LinearProgressIndicator(value: c.count / mine.first.count, minHeight: 8, backgroundColor: t.brandSoft, color: t.ai))),
                          SizedBox(width: 40, child: Text('${c.count}', textAlign: TextAlign.right, style: const TextStyle(fontWeight: FontWeight.w700))),
                        ]),
                      ),
                    if (last != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text('Last one ${_ago(last)}', style: TextStyle(color: t.fg2, fontSize: 12.5))),
                  ],
                ])),
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
    return FutureBuilder<(Health, List<AlertCount>, List<RecorderItem>, HealthDetail)>(
      future: Future.wait([repo.health(), repo.alertCounts(period), repo.recorders(), repo.healthDetail()]).then((r) => (r[0] as Health, r[1] as List<AlertCount>, r[2] as List<RecorderItem>, r[3] as HealthDetail)),
      builder: (context, snap) {
        if (snap.hasError) return EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load this', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}');
        if (!snap.hasData) return const SkeletonRows();
        final (h, counts, recorders, detail) = snap.data!;
        final reporting = recorders.where((r) => r.health != null).toList();
        final incidents = counts.where((c) => _healthAlerts.contains(c.rule) && c.rule != 'back_to_normal').toList()..sort((a, b) => b.count.compareTo(a.count));
        String title(String rule) => defs.where((d) => d.type == rule).firstOrNull?.title ?? rule.replaceAll('_', ' ');
        Widget card(String label, String value, {bool bad = false}) => Expanded(
              child: Container(
                padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
                decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.button), border: Border.all(color: t.line)),
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
          for (final r in reporting) ...[GroupLabel('${r.name} · disks, clock and footage'), RecorderHealthCard(r.health!)],
          if (detail.cameras.isNotEmpty) ...[
            const GroupLabel('Cameras'),
            Group(children: [for (final c in detail.cameras) _CameraRow(c)]),
          ],
          const GroupLabel('Last 7 days'),
          Group(children: [Padding(padding: EdgeInsets.all(cardPad(context)), child: _WeekStrip(detail.days))]),
          GroupLabel('Problems · ${period.label.toLowerCase()}'),
          if (incidents.isEmpty)
            Group(children: [Padding(padding: EdgeInsets.all(cardPad(context)), child: Row(children: [Icon(Icons.check_circle_rounded, color: t.okInk), const SizedBox(width: 12), const Expanded(child: Text('No problems in this period.', style: TextStyle(fontWeight: FontWeight.w600)))]))])
          else
            Group(children: [
              for (final c in incidents)
                Padding(
                  padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
                  child: Row(children: [
                    Badge2(['recorder_offline', 'recorder_restarted', 'clock_wrong'].contains(c.rule) ? Icons.dns_outlined : (['disk_fault', 'disk_wear', 'storage_full', 'recording_stopped'].contains(c.rule) ? Icons.storage_rounded : (c.rule == 'connection_unstable' ? Icons.wifi_off_rounded : Icons.videocam_off_outlined)), Level.bad, size: 40),
                    const SizedBox(width: 14),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(c.place.isEmpty ? title(c.rule) : c.place, style: const TextStyle(fontWeight: FontWeight.w700)),
                      Text([title(c.rule), if (c.last != null) 'last ${_ago(c.last)}'].join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
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

/// One camera: online or not, whether the recorder is saving its video, when it last sent a picture, and what is wrong with its picture, if anything.
class _CameraRow extends StatelessWidget {
  const _CameraRow(this.c);
  final CameraHealth c;

  static const _picture = {
    'moved': 'Its view has changed. Check that it has not been knocked.',
    'covered': 'Something is in front of it.',
    'blurry': 'The picture is blurry. Clean the lens.',
    'dark': 'The picture is too dark to be useful.',
  };

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final level = !c.online || c.recording == false ? Level.bad : (c.picture != null ? Level.warn : Level.ok);
    final (bg, fg) = levelColors(t, level);
    final recording = c.recording == null ? null : (c.recording! ? 'Recording' : 'Not recording');
    final lines = <String>[
      c.online ? 'Online' : 'Offline',
      if (recording != null) recording,
      if (c.secondsSincePicture != null) 'Picture ${shortAgo(c.secondsSincePicture)}',
    ];
    return Padding(
      padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12),
      child: Row(children: [
        Container(width: 40, height: 40, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(Radii.chip)), child: Icon(c.online ? Icons.videocam_outlined : Icons.videocam_off_outlined, color: fg, size: 21)),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(c.name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          Text(lines.join(' · '), style: TextStyle(color: t.fg2, fontSize: 12.5)),
          if (c.picture != null) Text(_picture[c.picture] ?? '', style: TextStyle(color: t.warnInk, fontSize: 12.5, fontWeight: FontWeight.w600)),
        ])),
      ]),
    );
  }
}

/// Seven dots, oldest first: green for a day with no problems, amber for a few, red for many, with the day's first letter under each.
class _WeekStrip extends StatelessWidget {
  const _WeekStrip(this.days);
  final List<DayProblems> days;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    const letters = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
    return Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
      for (final d in days)
        Semantics(
          label: '${d.day.day}/${d.day.month}: ${d.problems == 0 ? 'no problems' : '${d.problems} problems'}',
          child: Column(children: [
            Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(color: d.problems == 0 ? t.okSoft : (d.problems < 4 ? t.warnSoft : t.critSoft), shape: BoxShape.circle),
              child: d.problems == 0 ? Icon(Icons.check_rounded, size: 18, color: t.okInk) : Text('${d.problems}', style: TextStyle(fontWeight: FontWeight.w700, color: d.problems < 4 ? t.warnInk : t.crit)),
            ),
            const SizedBox(height: 4),
            Text(letters[d.day.weekday - 1], style: TextStyle(color: t.fg2, fontSize: 12)),
          ]),
        ),
    ]);
  }
}
