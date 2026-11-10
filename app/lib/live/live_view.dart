import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../geometry/polygon.dart';
import '../models.dart';
import '../repo.dart';
import '../theme.dart';
import '../widgets/brand.dart';
import 'live_rules.dart';

enum PictureSize { fit, fill, enlarge }

/// Live view and AI view of one camera. It closes by itself when the clock runs out, "Keep watching" adds time, and leaving the page stops it.
class LiveView extends StatefulWidget {
  const LiveView({super.key, required this.repo, required this.camera, this.compact = false});
  final Repository repo;
  final CameraItem camera;
  final bool compact; // phone layout: shows the Fit / Fill / Enlarge switch

  @override
  State<LiveView> createState() => _LiveViewState();
}

class _LiveViewState extends State<LiveView> with WidgetsBindingObserver {
  LiveMode mode = LiveMode.live;
  PictureSize size = PictureSize.fit;
  LiveSession? session;
  LiveClock? clock;
  Uint8List? image;
  String? error;
  Timer? _tick, _frames;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stopTimers();
    final s = session;
    if (s != null && !(clock?.ended ?? true)) widget.repo.stopLive(s).catchError((_) {}); // leaving the page ends the view
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState st) {
    if (st != AppLifecycleState.resumed && session != null) _stop('The view was closed because the app went to the background.');
  }

  void _stopTimers() {
    _tick?.cancel();
    _frames?.cancel();
  }

  Future<void> _start() async {
    if (_busy) return;
    setState(() { _busy = true; error = null; });
    try {
      final s = await widget.repo.startLive(widget.camera.id, mode);
      clock = LiveClock(mode);
      session = s;
      image = null;
      _tick = Timer.periodic(const Duration(seconds: 1), (_) => _onTick());
      _frames = Timer.periodic(const Duration(seconds: 1), (_) => _pull());
    } catch (e) {
      error = e.toString();
    }
    if (mounted) setState(() => _busy = false);
  }

  void _onTick() {
    final c = clock;
    if (c == null) return;
    c.tick();
    if (c.ended) {
      _stopTimers();
      error = mode == LiveMode.ai ? 'AI view closes after 30 seconds to save bandwidth.' : 'Live view closes after 2 minutes to save bandwidth.';
    }
    if (mounted) setState(() {});
  }

  Future<void> _pull() async {
    final s = session;
    if (s == null || (clock?.ended ?? true)) return;
    try {
      final b = await widget.repo.frame(s);
      if (mounted && b != null) setState(() => image = b);
    } catch (_) {/* the next picture will do */}
  }

  Future<void> _extend() async {
    final c = clock, s = session;
    if (c == null || s == null || !c.canExtend) return;
    try {
      await widget.repo.extendLive(s);
      setState(c.extend);
    } catch (e) {
      setState(() => error = e.toString());
    }
  }

  void _stop([String? why]) {
    final s = session;
    _stopTimers();
    clock?.stop();
    if (s != null) widget.repo.stopLive(s).catchError((_) {});
    setState(() { session = null; clock = null; image = null; error = why; });
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final c = clock;
    final running = session != null && c != null && !c.ended;
    final ended = c != null && c.ended;
    final h = MediaQuery.sizeOf(context).height;
    Widget scene = _Scene(ai: running && mode == LiveMode.ai, t: c?.elapsed ?? 0, image: running ? image : null, running: running, connecting: running && c.connecting, ended: ended, offline: !widget.camera.working, label: '${widget.camera.name} · ${running ? (mode == LiveMode.ai ? 'AI view' : 'Live') : 'Picture on request'}');
    final picture = switch (size) {
      PictureSize.fit => AspectRatio(aspectRatio: 16 / 9, child: scene),
      PictureSize.fill => SizedBox(height: h * 0.6, child: FittedBox(fit: BoxFit.cover, clipBehavior: Clip.hardEdge, child: SizedBox(width: 1600, height: 900, child: scene))),
      PictureSize.enlarge => SizedBox(height: h * 0.72, width: double.infinity, child: Center(child: RotatedBox(quarterTurns: 1, child: AspectRatio(aspectRatio: 16 / 9, child: scene)))),
    };
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SelectionContainer.disabled(child: ClipRRect(borderRadius: BorderRadius.circular(14), child: ColoredBox(color: const Color(0xFF243449), child: picture))),
      if (widget.compact) ...[
        const SizedBox(height: 10),
        SegmentedButton<PictureSize>(
          segments: const [ButtonSegment(value: PictureSize.fit, label: Text('Fit')), ButtonSegment(value: PictureSize.fill, label: Text('Fill')), ButtonSegment(value: PictureSize.enlarge, label: Text('Enlarge'))],
          selected: {size},
          onSelectionChanged: (v) => setState(() => size = v.first),
        ),
        const SizedBox(height: 4),
        Text(switch (size) {
          PictureSize.enlarge => 'Picture turned to use the whole phone height. Nothing is cropped. Tilt your phone to read it.',
          PictureSize.fill => 'Fills most of the screen. The sides are cropped.',
          PictureSize.fit => 'Whole picture. Turn your phone sideways for full screen.',
        }, style: TextStyle(color: t.fg2, fontSize: 12.5)),
      ],
      const SizedBox(height: 12),
      if (running) ...[
        LinearProgressIndicator(value: (c.left / c.rules.period).clamp(0.0, 1.0), minHeight: 6, borderRadius: BorderRadius.circular(3), color: mode == LiveMode.ai ? t.ai : t.brand, backgroundColor: t.line),
        const SizedBox(height: 8),
        Row(children: [
          Text('Closes in ${c.countdown}', style: const TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(width: 10),
          Expanded(child: Text(mode == LiveMode.ai ? 'AI view' : 'Up to 10 minutes in total', style: TextStyle(color: t.fg2, fontSize: 12.5))),
        ]),
        const SizedBox(height: 8),
        Row(children: [
          Expanded(child: OutlinedButton(onPressed: c.canExtend ? _extend : null, child: Text(c.canExtend ? 'Keep watching +${c.rules.period == 30 ? '30 s' : '2 min'}' : 'Time limit reached', overflow: TextOverflow.ellipsis))),
          const SizedBox(width: 8),
          OutlinedButton(onPressed: () => _stop(), child: const Text('Stop')),
        ]),
      ] else ...[
        if (!widget.camera.working)
          Text('This camera is offline. Live view is not available.', style: TextStyle(color: t.crit, fontWeight: FontWeight.w600))
        else ...[
          SegmentedButton<LiveMode>(
            segments: const [ButtonSegment(value: LiveMode.live, label: Text('Live view')), ButtonSegment(value: LiveMode.ai, label: Row(mainAxisSize: MainAxisSize.min, children: [AiSpark(size: 16), SizedBox(width: 6), Text('AI view')]))],
            selected: {mode},
            onSelectionChanged: (v) => setState(() => mode = v.first),
          ),
          const SizedBox(height: 10),
          FilledButton(onPressed: _busy ? null : _start, child: Text(ended ? 'Start again' : (mode == LiveMode.ai ? 'Start AI view' : 'Start live view'))),
          const SizedBox(height: 6),
          Text(mode == LiveMode.ai ? 'Shows what the AI sees. Closes after 30 seconds, up to 2 minutes in total.' : 'Closes after 2 minutes. Keep watching adds 2 more, up to 10 minutes.', style: TextStyle(color: t.fg2, fontSize: 12.5)),
        ],
        if (error != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text(error!, style: TextStyle(color: t.fg2))),
      ],
    ]);
  }
}

/// The demo picture: a still scene with people, and in AI view the boxes and the marked area drawn on it.
class _Scene extends StatelessWidget {
  const _Scene({required this.ai, required this.t, required this.image, required this.running, required this.connecting, required this.ended, required this.offline, required this.label});
  final bool ai, running, connecting, ended, offline;
  final int t;
  final Uint8List? image;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Stack(fit: StackFit.expand, children: [
      if (image != null) Image.memory(image!, fit: BoxFit.cover, gaplessPlayback: true) else CustomPaint(painter: _ScenePainter(ai: ai, t: t, people: running && !connecting)),
      if (running && !connecting) Positioned(right: 10, top: 8, child: ai ? const _AiTag() : const _Tag('● Live')),
      if (connecting) const _Cover('Connecting to the camera…'),
      if (ended) const _Cover('Live view stopped'),
      if (offline) const _Cover('This camera is offline'),
      Positioned(left: 10, bottom: 8, child: _Tag(label)),
    ]);
  }
}

class _AiTag extends StatelessWidget {
  const _AiTag();
  @override
  Widget build(BuildContext context) => Container(
      padding: const EdgeInsets.fromLTRB(6, 3, 10, 3),
      decoration: BoxDecoration(gradient: aiGradient, borderRadius: BorderRadius.circular(99)),
      child: const Row(mainAxisSize: MainAxisSize.min, children: [AiSpark(size: 15, color: Colors.white), SizedBox(width: 4), Text('AI view', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w700))]));
}

class _Tag extends StatelessWidget {
  const _Tag(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Container(padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(8)), child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 12)));
}

class _Cover extends StatelessWidget {
  const _Cover(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => ColoredBox(color: const Color(0x8C0A1018), child: Center(child: Text(text, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700))));
}

class _ScenePainter extends CustomPainter {
  _ScenePainter({required this.ai, required this.t, required this.people});
  final bool ai, people;
  final int t;
  static const _base = [Offset(.22, .30), Offset(.41, .36), Offset(.66, .30)];

  @override
  void paint(Canvas canvas, Size s) {
    canvas.drawRect(Offset.zero & s, Paint()..shader = const LinearGradient(colors: [Color(0xFF243449), Color(0xFF3A5272)], begin: Alignment.topLeft, end: Alignment.bottomRight).createShader(Offset.zero & s));
    if (!people) return;
    const zone = [Pt(.18, .30), Pt(.55, .26), Pt(.64, .58), Pt(.42, .78), Pt(.20, .66)];
    if (ai) {
      final path = Path()..moveTo(zone[0].x * s.width, zone[0].y * s.height);
      for (final p in zone.skip(1)) {
        path.lineTo(p.x * s.width, p.y * s.height);
      }
      path.close();
      canvas.drawPath(path, Paint()..color = const Color(0xFF6BE0FF).withValues(alpha: .2));
      canvas.drawPath(path, Paint()..color = const Color(0xFF6BE0FF)..style = PaintingStyle.stroke..strokeWidth = 2);
    }
    var inZone = 0;
    for (var i = 0; i < _base.length; i++) {
      final x = _base[i].dx + math.sin(t / 2 + i) * 0.02, y = _base[i].dy;
      final r = Rect.fromLTWH(x * s.width, y * s.height, s.width * .07, s.height * .32);
      canvas.drawRRect(RRect.fromRectAndRadius(r, const Radius.circular(10)), Paint()..color = const Color(0x8CEBF0F8));
      if (ai) {
        canvas.drawRRect(RRect.fromRectAndRadius(r.inflate(3), const Radius.circular(6)), Paint()..color = const Color(0xFFB5A2FF)..style = PaintingStyle.stroke..strokeWidth = 2);
        if (pointInPolygon(zone, Pt(x + .035, y + .16))) inZone++;
        final tp = TextPainter(text: TextSpan(text: 'Person ${88 + i * 3}%', style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w700, backgroundColor: Color(0xFF5B3DE0))), textDirection: TextDirection.ltr)..layout();
        tp.paint(canvas, Offset(r.left - 3, r.top - 20));
      }
    }
    if (ai) {
      final tp = TextPainter(text: TextSpan(text: 'In the area: $inZone', style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w700, backgroundColor: Color(0xFF5B3DE0))), textDirection: TextDirection.ltr)..layout();
      tp.paint(canvas, Offset(s.width * .18, s.height * .20));
    }
  }

  @override
  bool shouldRepaint(_ScenePainter o) => o.t != t || o.ai != ai || o.people != people;
}
