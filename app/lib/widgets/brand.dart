import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import '../theme.dart';

/// Jas iVision's own logo, from the brand files (globe in an AI detection frame, camera-lens "o", tagline).
enum LogoStyle { full, compact, mark }

const tagline = 'Intelligent Vision. Automated Insights.';

class BrandLogo extends StatelessWidget {
  const BrandLogo({super.key, this.style = LogoStyle.compact, this.height = 36, this.onDark});
  final LogoStyle style;
  final double height;
  final bool? onDark; // force the dark-background version (for example on the blue panel)

  @override
  Widget build(BuildContext context) {
    final dark = onDark ?? Theme.of(context).brightness == Brightness.dark;
    final asset = switch (style) {
      LogoStyle.full => dark ? 'logo-on-dark' : 'logo-on-light',
      LogoStyle.compact => dark ? 'logo-compact-on-dark' : 'logo-compact',
      LogoStyle.mark => 'logo-mark',
    };
    return Semantics(label: 'Jas iVision', image: true, child: SvgPicture.asset('assets/brand/$asset.svg', height: height, excludeFromSemantics: true));
  }
}

/// The AI sparkle from the logo: a four-pointed star, cyan to violet. Smart Alerts and AI view always carry it.
class AiSpark extends StatelessWidget {
  const AiSpark({super.key, this.size = 18, this.color});
  final double size;
  final Color? color; // a flat colour, for example white on a coloured badge
  @override
  Widget build(BuildContext context) => SizedBox(width: size, height: size, child: CustomPaint(painter: _SparkPainter(color)));
}

class _SparkPainter extends CustomPainter {
  _SparkPainter(this.color);
  final Color? color;

  static Path star(Offset c, double r) {
    final k = r * .18;
    return Path()
      ..moveTo(c.dx, c.dy - r)
      ..quadraticBezierTo(c.dx + k, c.dy - k, c.dx + r, c.dy)
      ..quadraticBezierTo(c.dx + k, c.dy + k, c.dx, c.dy + r)
      ..quadraticBezierTo(c.dx - k, c.dy + k, c.dx - r, c.dy)
      ..quadraticBezierTo(c.dx - k, c.dy - k, c.dx, c.dy - r)
      ..close();
  }

  @override
  void paint(Canvas canvas, Size s) {
    final big = Rect.fromLTWH(0, 0, s.width, s.height);
    final paint = Paint()
      ..shader = color == null ? const LinearGradient(colors: [Color(0xFF35C6F4), Color(0xFF7C5CFF)], begin: Alignment.topLeft, end: Alignment.bottomRight).createShader(big) : null
      ..color = color ?? Colors.white;
    canvas.drawPath(star(Offset(s.width * .42, s.height * .58), s.width * .42), paint);
    canvas.drawPath(star(Offset(s.width * .8, s.height * .2), s.width * .17), paint);
  }

  @override
  bool shouldRepaint(_SparkPainter o) => o.color != color;
}

/// The corner brackets of the logo's detection frame, as a decoration.
class FramePainter extends CustomPainter {
  FramePainter({required this.color, this.inset = 14, this.arm = 22, this.width = 3});
  final Color color;
  final double inset, arm, width;
  @override
  void paint(Canvas canvas, Size s) {
    final p = Paint()..color = color..style = PaintingStyle.stroke..strokeWidth = width..strokeCap = StrokeCap.round;
    final l = inset, t = inset, r = s.width - inset, b = s.height - inset;
    canvas.drawPath(Path()..moveTo(l, t + arm)..lineTo(l, t)..lineTo(l + arm, t), p);
    canvas.drawPath(Path()..moveTo(r - arm, t)..lineTo(r, t)..lineTo(r, t + arm), p);
    canvas.drawPath(Path()..moveTo(r, b - arm)..lineTo(r, b)..lineTo(r - arm, b), p);
    canvas.drawPath(Path()..moveTo(l + arm, b)..lineTo(l, b)..lineTo(l, b - arm), p);
  }

  @override
  bool shouldRepaint(FramePainter o) => o.color != color;
}

/// A soft glow that hints at a camera lens, for hero panels.
class LensGlow extends StatelessWidget {
  const LensGlow({super.key, this.size = 220});
  final double size;
  @override
  Widget build(BuildContext context) => IgnorePointer(
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(shape: BoxShape.circle, gradient: RadialGradient(colors: [const Color(0xFF35C6F4).withValues(alpha: .35), const Color(0xFF35C6F4).withValues(alpha: 0)])),
          child: Center(child: Transform.rotate(angle: -math.pi / 12, child: AiSpark(size: size * .28, color: Colors.white.withValues(alpha: .85)))),
        ),
      );
}

const brandGradient = LinearGradient(colors: [Color(0xFF0A3D73), Color(0xFF128BE0)], begin: Alignment.topLeft, end: Alignment.bottomRight);
const aiGradient = LinearGradient(colors: [Color(0xFF35C6F4), Color(0xFF7C5CFF)], begin: Alignment.topLeft, end: Alignment.bottomRight);

/// The "Smart" label with the sparkle.
class SmartTag extends StatelessWidget {
  const SmartTag({super.key, this.label = 'Smart'});
  final String label;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(6, 3, 10, 3),
      decoration: BoxDecoration(color: t.aiSoft, borderRadius: BorderRadius.circular(Radii.pill)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [const AiSpark(size: 15), const SizedBox(width: 4), Text(label, style: TextStyle(color: t.ai, fontWeight: FontWeight.w700, fontSize: 12))]),
    );
  }
}

/// The logo mark, alive: the detection frame breathes, a scan line sweeps across it and the AI sparkle twinkles. No tagline, so it works at small sizes.
/// Used wherever the app is loading.
class LogoLoader extends StatefulWidget {
  const LogoLoader({super.key, this.size = 64, this.label});
  final double size;
  final String? label; // read out by screen readers, for example "Loading your cameras"
  @override
  State<LogoLoader> createState() => _LogoLoaderState();
}

class _LogoLoaderState extends State<LogoLoader> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 2200))..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.size;
    return Semantics(
      label: widget.label ?? 'Loading',
      liveRegion: true,
      child: ExcludeSemantics(
        child: SizedBox(
          width: s,
          height: s,
          child: AnimatedBuilder(
            animation: _c,
            builder: (context, _) {
              final t = _c.value;
              final breathe = 1 + .04 * math.sin(t * 2 * math.pi);
              return Stack(clipBehavior: Clip.none, alignment: Alignment.center, children: [
                Transform.scale(scale: breathe, child: SvgPicture.asset('assets/brand/logo-mark.svg', width: s, height: s)),
                Positioned.fill(child: CustomPaint(painter: _ScanPainter(t))),
                // the sparkle in the globe's orbit gap twinkles
                Positioned(
                  left: s * .64 - s * .17,
                  top: s * .29 - s * .17,
                  child: Transform.scale(scale: .75 + .45 * (.5 + .5 * math.sin(t * 2 * math.pi * 2)), child: Transform.rotate(angle: t * math.pi, child: AiSpark(size: s * .34))),
                ),
              ]);
            },
          ),
        ),
      ),
    );
  }
}

class _ScanPainter extends CustomPainter {
  _ScanPainter(this.t);
  final double t;
  @override
  void paint(Canvas canvas, Size s) {
    // a soft line that travels down inside the frame, then fades
    final top = s.height * .17, bottom = s.height * .83;
    final p = Curves.easeInOut.transform((t * 1.25).clamp(0.0, 1.0));
    final y = top + (bottom - top) * p;
    final fade = t < .8 ? 1.0 : (1 - (t - .8) / .2);
    final rect = Rect.fromLTRB(s.width * .17, y - 5, s.width * .83, y + 5);
    final paint = Paint()
      ..shader = LinearGradient(colors: [Colors.transparent, const Color(0xFF35C6F4).withValues(alpha: .9 * fade), Colors.transparent], begin: Alignment.centerLeft, end: Alignment.centerRight).createShader(rect);
    canvas.drawRRect(RRect.fromRectAndRadius(Rect.fromLTRB(rect.left, y - 1.5, rect.right, y + 1.5), const Radius.circular(2)), paint);
    final glow = Paint()
      ..shader = LinearGradient(colors: [const Color(0xFF35C6F4).withValues(alpha: .0), const Color(0xFF35C6F4).withValues(alpha: .18 * fade)], begin: Alignment.topCenter, end: Alignment.bottomCenter).createShader(Rect.fromLTRB(rect.left, y - 22, rect.right, y));
    canvas.drawRect(Rect.fromLTRB(rect.left, y - 22, rect.right, y), glow);
  }

  @override
  bool shouldRepaint(_ScanPainter o) => o.t != t;
}

/// A centred loading state: the animated logo and, optionally, a line of text.
class Loading extends StatelessWidget {
  const Loading({super.key, this.text, this.size = 60});
  final String? text;
  final double size;
  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          LogoLoader(size: size, label: text),
          if (text != null) ...[const SizedBox(height: 14), Text(text!, style: TextStyle(color: Tok.of(context).fg2, fontWeight: FontWeight.w500))],
        ]),
      );
}
