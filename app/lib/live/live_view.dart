import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../geometry/polygon.dart';
import '../models.dart';
import '../repo.dart';
import '../screens/common.dart' show Segments;
import '../theme.dart';
import '../widgets/brand.dart';
import 'live_rules.dart';
import 'webrtc_player.dart';

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
  StreamSubscription<Uint8List>? _video;      // moving pictures; if it cannot start or drops, a picture every second takes over
  Widget? _smooth;                            // the WebRTC player while it is the picture; null means pictures
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
    _video?.cancel();
    _video = null;
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
      if (mode == LiveMode.live) { _openVideo(s); } else { _startPictures(); }
    } catch (e) {
      error = e.toString();
    }
    if (mounted) setState(() => _busy = false);
  }

  void _startPictures() {
    _frames?.cancel();
    _frames = Timer.periodic(const Duration(seconds: 1), (_) => _pull());
  }

  Future<void> _openVideo(LiveSession s) async {
    // Smooth video first when the app can do it; the picture stream is started by _fallBack if it does not come up.
    if (widget.repo.smoothVideo) {
      setState(() => _smooth = LiveWebRtc(key: ValueKey('smooth-${s.id}'), repo: widget.repo, session: s, onFallback: () => _fallBack(s)));
      return;
    }
    try {
      final v = await widget.repo.liveStream(s);
      if (!mounted || session != s) return;
      if (v == null) return _startPictures();
      _video = v.listen(
        (b) { if (mounted) setState(() => image = b); },
        onError: (_) => _fallBack(s),
        onDone: () => _fallBack(s),
        cancelOnError: true,
      );
    } catch (_) {
      _fallBack(s);
    }
  }

  /// The video stopped before the view's time was up (a weak connection, or the server was busy): carry on with pictures.
  void _fallBack(LiveSession s) {
    if (!mounted || session != s || (clock?.ended ?? true)) return;
    _video = null;
    setState(() => _smooth = null);
    _startPictures();
  }

  void _onTick() {
    final c = clock;
    if (c == null) return;
    c.tick();
    if (c.ended) {
      _stopTimers();
      _smooth = null;
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
    setState(() { session = null; clock = null; image = null; _smooth = null; error = why; });
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final c = clock;
    final running = session != null && c != null && !c.ended;
    final ended = c != null && c.ended;
    final h = MediaQuery.sizeOf(context).height;
    Widget scene = _Scene(ring: running ? (c.left / c.rules.period).clamp(0.0, 1.0) : null, ringText: running ? c.countdown : '', idle: !running && !ended && widget.camera.working, ai: running && mode == LiveMode.ai, t: c?.elapsed ?? 0, image: running ? image : null, video: running ? _smooth : null, running: running, connecting: running && c.connecting, ended: ended, offline: !widget.camera.working, label: '${widget.camera.name} · ${running ? (mode == LiveMode.ai ? 'AI view' : 'Live') : 'Picture on request'}');
    final picture = switch (size) {
      PictureSize.fit => AspectRatio(aspectRatio: 16 / 9, child: scene),
      PictureSize.fill => SizedBox(height: h * 0.6, child: FittedBox(fit: BoxFit.cover, clipBehavior: Clip.hardEdge, child: SizedBox(width: 1600, height: 900, child: scene))),
      PictureSize.enlarge => SizedBox(height: h * 0.72, width: double.infinity, child: Center(child: RotatedBox(quarterTurns: 1, child: AspectRatio(aspectRatio: 16 / 9, child: scene)))),
    };
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SelectionContainer.disabled(child: ClipRRect(borderRadius: BorderRadius.circular(Radii.tile), child: ColoredBox(color: const Color(0xFF243449), child: picture))),
      if (widget.compact) ...[
        const SizedBox(height: 10),
        Segments<PictureSize>(
          items: const [(PictureSize.fit, 'Fit'), (PictureSize.fill, 'Fill'), (PictureSize.enlarge, 'Enlarge')],
          value: size,
          onChanged: (v) => setState(() => size = v),
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
          Row(children: [
            Expanded(child: _ModeCard(on: mode == LiveMode.live, onTap: () => setState(() => mode = LiveMode.live), icon: const Icon(Icons.videocam_outlined, size: 22), title: 'Live view', sub: '2 minutes')),
            const SizedBox(width: 10),
            Expanded(child: _ModeCard(on: mode == LiveMode.ai, onTap: () => setState(() => mode = LiveMode.ai), icon: const AiSpark(size: 22), title: 'AI view', sub: '30 seconds', ai: true)),
          ]),
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
  const _Scene({required this.ring, required this.ringText, required this.idle, required this.ai, required this.t, required this.image, required this.running, required this.connecting, required this.ended, required this.offline, required this.label, this.video});
  final bool ai, running, connecting, ended, offline, idle;
  final double? ring;
  final String ringText;
  final int t;
  final Uint8List? image;
  final Widget? video;          // the WebRTC player, once it has produced a frame
  final String label;

  @override
  Widget build(BuildContext context) {
    return Stack(fit: StackFit.expand, children: [
      if (video != null) video! else if (image != null) Image.memory(image!, fit: BoxFit.cover, gaplessPlayback: true) else CustomPaint(painter: _ScenePainter(ai: ai, t: t, people: running && !connecting)),
      if (running && !connecting) Positioned(left: 10, top: 10, child: ai ? const _AiTag() : const _LiveTag()),
      if (ring != null && !connecting) Positioned(right: 10, top: 10, child: _Ring(progress: ring!, text: ringText, ai: ai)),
      if (idle) Center(child: Container(width: 64, height: 64, decoration: BoxDecoration(color: Colors.white.withValues(alpha: .2), shape: BoxShape.circle, border: Border.all(color: Colors.white.withValues(alpha: .6), width: 2)), child: const Icon(Icons.play_arrow_rounded, color: Colors.white, size: 38))),
      if (connecting) const _Cover('Connecting to the camera…'),
      if (ended) const _Cover('Live view stopped'),
      if (offline) const _Cover('This camera is offline'),
      Positioned(left: 10, bottom: 8, child: _Tag(label)),
    ]);
  }
}

class _ModeCard extends StatelessWidget {
  const _ModeCard({required this.on, required this.onTap, required this.icon, required this.title, required this.sub, this.ai = false});
  final bool on, ai;
  final VoidCallback onTap;
  final Widget icon;
  final String title, sub;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final c = ai ? t.ai : t.brand;
    return Semantics(
      button: true,
      selected: on,
      child: InkWell(
        borderRadius: BorderRadius.circular(Radii.button),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: on ? (ai ? t.aiSoft : t.brandSoft) : t.surface, borderRadius: BorderRadius.circular(Radii.button), border: Border.all(color: on ? c : t.line, width: on ? 2 : 1.5)),
          child: Row(children: [
            icon,
            const SizedBox(width: 10),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: TextStyle(fontWeight: FontWeight.w700, color: on ? c : t.fg)), Text(sub, style: TextStyle(color: t.fg2, fontSize: 12))])),
          ]),
        ),
      ),
    );
  }
}

class _LiveTag extends StatefulWidget {
  const _LiveTag();
  @override
  State<_LiveTag> createState() => _LiveTagState();
}

class _LiveTagState extends State<_LiveTag> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))..repeat(reverse: true);
  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Container(
      padding: const EdgeInsets.fromLTRB(9, 4, 11, 4),
      decoration: BoxDecoration(color: Colors.black.withValues(alpha: .55), borderRadius: BorderRadius.circular(Radii.pill)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        FadeTransition(opacity: Tween(begin: .35, end: 1.0).animate(_c), child: Container(width: 9, height: 9, decoration: const BoxDecoration(color: Color(0xFFFF4D3D), shape: BoxShape.circle))),
        const SizedBox(width: 6),
        const Text('Live', style: TextStyle(color: Colors.white, fontSize: 12.5, fontWeight: FontWeight.w700)),
      ]));
}

/// The time left, as a ring around the seconds, on the corner of the picture.
class _Ring extends StatelessWidget {
  const _Ring({required this.progress, required this.text, required this.ai});
  final double progress;
  final String text;
  final bool ai;
  @override
  Widget build(BuildContext context) => Container(
      width: 52,
      height: 52,
      decoration: BoxDecoration(color: Colors.black.withValues(alpha: .5), shape: BoxShape.circle),
      child: Stack(alignment: Alignment.center, children: [
        SizedBox(width: 44, height: 44, child: CircularProgressIndicator(value: progress, strokeWidth: 3.5, color: ai ? const Color(0xFFB5A2FF) : const Color(0xFF6BE0FF), backgroundColor: Colors.white.withValues(alpha: .2))),
        Text(text, style: const TextStyle(color: Colors.white, fontSize: 11.5, fontWeight: FontWeight.w700, fontFeatures: [FontFeature.tabularFigures()])),
      ]));
}

class _AiTag extends StatelessWidget {
  const _AiTag();
  @override
  Widget build(BuildContext context) => Container(
      padding: const EdgeInsets.fromLTRB(6, 3, 10, 3),
      decoration: BoxDecoration(gradient: aiGradient, borderRadius: BorderRadius.circular(Radii.pill)),
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
