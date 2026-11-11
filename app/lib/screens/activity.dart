
import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'common.dart';

/// Activity: people counted over time. Opened from More. (Number plates have their own page: Vehicles.)
class ActivityPage extends StatelessWidget {
  const ActivityPage({super.key, required this.repo});
  final Repository repo;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Activity'), bottom: PreferredSize(preferredSize: const Size.fromHeight(1), child: Divider(height: 1, color: t.line))),
      body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 900), child: PeopleTab(repo: repo))),
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
