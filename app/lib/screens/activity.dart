import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

/// Activity: people counted over time, and the number plates read. Opened from More.
class ActivityPage extends StatefulWidget {
  const ActivityPage({super.key, required this.repo});
  final Repository repo;
  @override
  State<ActivityPage> createState() => _ActivityPageState();
}

class _ActivityPageState extends State<ActivityPage> {
  bool vehicles = false;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Activity'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 900),
          child: Column(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: SizedBox(
                width: double.infinity,
                child: SegmentedButton<bool>(
                  showSelectedIcon: false,
                  segments: const [ButtonSegment(value: false, label: Text('People'), icon: Icon(Icons.groups_outlined)), ButtonSegment(value: true, label: Text('Vehicles'), icon: Icon(Icons.directions_car_outlined))],
                  selected: {vehicles},
                  onSelectionChanged: (v) => setState(() => vehicles = v.first),
                ),
              ),
            ),
            Expanded(child: vehicles ? VehiclesTab(repo: widget.repo) : PeopleTab(repo: widget.repo)),
          ]),
        ),
      ),
    );
  }
}

// ----------------------------------------------------------------------------------------------------------------------------- people
class PeopleTab extends StatefulWidget {
  const PeopleTab({super.key, required this.repo});
  final Repository repo;
  @override
  State<PeopleTab> createState() => _PeopleTabState();
}

class _PeopleTabState extends State<PeopleTab> {
  int days = 1;
  String? cameraId;
  late Future<(FootfallRange, List<CameraItem>)> data = _load();

  Future<(FootfallRange, List<CameraItem>)> _load() async {
    final r = await Future.wait([widget.repo.footfallRange(days: days, cameraId: cameraId), widget.repo.cameras()]);
    return (r[0] as FootfallRange, r[1] as List<CameraItem>);
  }

  void _set({int? d, String? cam, bool all = false}) => setState(() {
        if (d != null) days = d;
        if (cam != null || all) cameraId = all ? null : cam;
        data = _load();
      });

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return FutureBuilder(
      future: data,
      builder: (context, snap) {
        final cams = snap.data?.$2 ?? const <CameraItem>[];
        Widget chip(String label, bool on, VoidCallback tap) => Padding(padding: const EdgeInsets.only(right: 8), child: ChoiceChip(label: Text(label), selected: on, onSelected: (_) => tap(), showCheckmark: false));
        return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 40), children: [
          SingleChildScrollView(scrollDirection: Axis.horizontal, child: Row(children: [chip('Today', days == 1, () => _set(d: 1)), chip('7 days', days == 7, () => _set(d: 7)), chip('30 days', days == 30, () => _set(d: 30))])),
          if (cams.length > 1)
            Padding(padding: const EdgeInsets.only(top: 8), child: SingleChildScrollView(scrollDirection: Axis.horizontal, child: Row(children: [chip('All cameras', cameraId == null, () => _set(all: true)), for (final c in cams) chip(c.name, cameraId == c.id, () => _set(cam: c.id))]))),
          const SizedBox(height: 14),
          if (snap.hasError)
            EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the counts', text: snap.error is ApiError ? (snap.error as ApiError).message : '${snap.error}', action: OutlinedButton(onPressed: () => _set(), child: const Text('Try again')))
          else if (!snap.hasData)
            const Padding(padding: EdgeInsets.all(40), child: Loading())
          else ...[
            _Kpis(snap.data!.$1),
            const GroupLabel('Over time'),
            Group(padding: EdgeInsets.all(cardPad(context)), children: [
              Row(children: [Expanded(child: Text(days == 1 ? 'By hour' : 'By day', style: TextStyle(color: t.fg2))), _Dot(t.brand, 'In'), const SizedBox(width: 14), _Dot(t.fg2.withValues(alpha: .55), 'Out')]),
              const SizedBox(height: 12),
              snap.data!.$1.bars.any((b) => b.peopleIn > 0 || b.peopleOut > 0)
                  ? SizedBox(height: 190, child: CustomPaint(size: Size.infinite, painter: _BarsPainter(snap.data!.$1.bars, t)))
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

// --------------------------------------------------------------------------------------------------------------------------- vehicles
class VehiclesTab extends StatefulWidget {
  const VehiclesTab({super.key, required this.repo});
  final Repository repo;
  @override
  State<VehiclesTab> createState() => _VehiclesTabState();
}

class _VehiclesTabState extends State<VehiclesTab> {
  final q = TextEditingController();
  final items = <PlateRead>[];
  String? next;
  bool loading = true, loadingMore = false;
  String? error;
  Timer? _debounce;
  List<CameraItem> cams = [];

  @override
  void initState() {
    super.initState();
    widget.repo.cameras().then((c) { if (mounted) setState(() => cams = c); }).catchError((_) {});
    _load(reset: true);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _load({required bool reset}) async {
    setState(() { if (reset) { loading = true; items.clear(); next = null; } else { loadingMore = true; } error = null; });
    try {
      final p = await widget.repo.plates(q: q.text, cursor: reset ? null : next);
      if (!mounted) return;
      setState(() { items.addAll(p.items); next = p.next; });
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (_) {
      if (mounted) setState(() => error = 'We could not load the plates.');
    }
    if (mounted) setState(() { loading = false; loadingMore = false; });
  }

  String _when(DateTime? d) {
    if (d == null) return '';
    final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
    const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return '${d.day} ${m[d.month - 1]}, $h:${d.minute.toString().padLeft(2, '0')} ${d.hour < 12 ? 'am' : 'pm'}';
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 40), children: [
      TextField(
        controller: q,
        maxLength: 20,
        textCapitalization: TextCapitalization.characters,
        decoration: const InputDecoration(prefixIcon: Icon(Icons.search_rounded), hintText: 'Search a plate, for example GJ14', counterText: ''),
        onChanged: (_) {
          _debounce?.cancel();
          _debounce = Timer(const Duration(milliseconds: 350), () => _load(reset: true));
        },
      ),
      const SizedBox(height: 12),
      if (error != null)
        EmptyState(icon: Icons.cloud_off_outlined, title: 'We could not load the plates', text: error!, action: OutlinedButton(onPressed: () => _load(reset: true), child: const Text('Try again')))
      else if (loading)
        const Padding(padding: EdgeInsets.all(40), child: Loading())
      else if (items.isEmpty)
        Padding(padding: const EdgeInsets.all(32), child: Center(child: Text(q.text.isEmpty ? 'No plates read in the last 7 days.' : 'No plates match.', style: TextStyle(color: t.fg2))))
      else ...[
        Group(children: [
          for (final p in items)
            InkWell(
              onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => PlateDetail(repo: widget.repo, read: p, cameraName: cams.where((c) => c.id == p.cameraId).firstOrNull?.name))),
              child: Padding(
                padding: EdgeInsets.fromLTRB(cardPad(context), 12, 12, 12),
                child: Row(children: [
                  Badge2(Icons.directions_car_outlined, p.blocked ? Level.bad : Level.mute, size: 40),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(p.plate ?? 'unreadable', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, letterSpacing: 1, color: p.blocked ? t.crit : t.fg)),
                      Text([_when(p.seen), if (p.direction != null) (p.direction == 'in' ? 'In' : 'Out'), if (p.vehicle != null) p.vehicle!].where((x) => x.isNotEmpty).join(' · '), style: TextStyle(color: t.fg2, fontSize: 13)),
                    ]),
                  ),
                  if (p.blocked) const Pill('Blocked list', Level.bad) else if (p.needsReview) const Pill('Needs a check', Level.warn),
                  Icon(Icons.chevron_right_rounded, color: t.fg2.withValues(alpha: .6)),
                ]),
              ),
            ),
        ]),
        if (next != null) Padding(padding: const EdgeInsets.only(top: 12), child: Center(child: OutlinedButton(onPressed: loadingMore ? null : () => _load(reset: false), child: Text(loadingMore ? 'Loading…' : 'Show more')))),
      ],
    ]);
  }
}

/// One plate read: the picture taken at the time and what is known about it.
class PlateDetail extends StatefulWidget {
  const PlateDetail({super.key, required this.repo, required this.read, this.cameraName});
  final Repository repo;
  final PlateRead read;
  final String? cameraName;
  @override
  State<PlateDetail> createState() => _PlateDetailState();
}

class _PlateDetailState extends State<PlateDetail> {
  late final Future<Uint8List?> picture = widget.read.hasPicture ? widget.repo.plateImage(widget.read.id) : Future.value(null);

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final p = widget.read;
    Widget row(String k, String v) => Padding(padding: EdgeInsets.fromLTRB(cardPad(context), 12, cardPad(context), 12), child: Row(children: [SizedBox(width: 110, child: Text(k, style: TextStyle(color: t.fg2))), Expanded(child: Text(v, style: const TextStyle(fontWeight: FontWeight.w600)))]));
    return Scaffold(
      appBar: AppBar(title: const Text('Number plate'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 700),
          child: ListView(padding: const EdgeInsets.all(16), children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: AspectRatio(
                aspectRatio: 16 / 9,
                child: FutureBuilder<Uint8List?>(
                  future: picture,
                  builder: (context, s) => s.data != null
                      ? Image.memory(s.data!, fit: BoxFit.cover)
                      : Container(color: t.surface2, alignment: Alignment.center, child: Text(!p.hasPicture ? 'No picture was saved for this read.' : (s.connectionState == ConnectionState.waiting ? 'Getting the picture…' : 'The picture is no longer stored.'), style: TextStyle(color: t.fg2))),
                ),
              ),
            ),
            const SizedBox(height: 16),
            Center(child: Text(p.plate ?? 'unreadable', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: 2, color: p.blocked ? t.crit : t.fg))),
            if (p.blocked) const Padding(padding: EdgeInsets.only(top: 8), child: Center(child: Pill('Blocked list', Level.bad))),
            if (p.needsReview) Padding(padding: const EdgeInsets.only(top: 12), child: Text('The reader was not sure about this plate.', textAlign: TextAlign.center, style: TextStyle(color: t.warnInk, fontWeight: FontWeight.w600))),
            const SizedBox(height: 14),
            Group(children: [
              row('Seen', p.seen == null ? '—' : '${p.seen!.day}/${p.seen!.month}/${p.seen!.year} ${p.seen!.hour.toString().padLeft(2, '0')}:${p.seen!.minute.toString().padLeft(2, '0')}'),
              row('Camera', widget.cameraName ?? '—'),
              row('Direction', p.direction == null ? '—' : (p.direction == 'in' ? 'In' : 'Out')),
              row('Vehicle', p.vehicle ?? '—'),
              row('Confidence', p.confidence == null ? '—' : '${p.confidence}%'),
              if (p.dwellMinutes != null) row('Time inside', '${p.dwellMinutes} min'),
            ]),
          ]),
        ),
      ),
    );
  }
}
