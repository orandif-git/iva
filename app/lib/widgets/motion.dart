import 'package:flutter/material.dart';

import '../theme.dart';

/// True when the person asked their device for less motion: nothing in the app animates then.
bool reduceMotion(BuildContext c) => MediaQuery.maybeOf(c)?.disableAnimations ?? false;

/// A screen (or a part of one) fades in and rises a little when it appears. Give it a new [key] to play it again, for example when the tab changes.
class RiseIn extends StatelessWidget {
  const RiseIn({super.key, required this.child, this.delay = Duration.zero});
  final Widget child;
  final Duration delay;
  @override
  Widget build(BuildContext context) {
    if (reduceMotion(context)) return child;
    final total = Motion.rise + delay;
    final start = delay.inMilliseconds / total.inMilliseconds;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: total,
      curve: Interval(start, 1, curve: Motion.ease),
      child: child,
      builder: (context, v, child) => Opacity(opacity: v, child: Transform.translate(offset: Offset(0, 14 * (1 - v)), child: child)),
    );
  }
}

/// A grey block with a light band passing over it: the shape of what is loading.
class Skeleton extends StatefulWidget {
  const Skeleton({super.key, this.width, this.height = 16, this.radius = 10});
  final double? width;
  final double height, radius;
  @override
  State<Skeleton> createState() => _SkeletonState();
}

class _SkeletonState extends State<Skeleton> with SingleTickerProviderStateMixin {
  AnimationController? _c;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (reduceMotion(context)) {
      _c?.dispose();
      _c = null;
    } else {
      _c ??= AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))..repeat();
    }
  }

  @override
  void dispose() {
    _c?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final base = t.line, light = Color.lerp(t.line, t.surface, .6)!;
    Widget box(double v) => Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(widget.radius),
            gradient: LinearGradient(colors: [base, light, base], stops: const [.25, .5, .75], begin: Alignment(-3 + 4 * v, 0), end: Alignment(-1 + 4 * v, 0)),
          ),
        );
    final c = _c;
    return c == null ? box(.5) : AnimatedBuilder(animation: c, builder: (_, __) => box(c.value));
  }
}

/// What a whole screen shows while it loads: a title, a line, a large card, a strip and two tiles, all shimmering. [label] is read out by screen readers.
class SkeletonPage extends StatelessWidget {
  const SkeletonPage({super.key, this.label = 'Loading', this.padding});
  final String label;
  final EdgeInsets? padding;
  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    return Semantics(
      label: label,
      liveRegion: true,
      child: ExcludeSemantics(
        child: SingleChildScrollView(
          physics: const NeverScrollableScrollPhysics(),
          padding: padding ?? EdgeInsets.fromLTRB(wide ? 32 : 16, 16, wide ? 32 : 16, 16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const FractionallySizedBox(widthFactor: .62, child: Skeleton(height: 30)),
            const SizedBox(height: 14),
            const FractionallySizedBox(widthFactor: .44, child: Skeleton(height: 22, radius: 11)),
            const SizedBox(height: 14),
            const Skeleton(height: 210, radius: Radii.card),
            const SizedBox(height: 14),
            const Skeleton(height: 84, radius: Radii.card),
            const SizedBox(height: 14),
            Row(children: const [Expanded(child: Skeleton(height: 120, radius: Radii.tile)), SizedBox(width: 12), Expanded(child: Skeleton(height: 120, radius: Radii.tile))]),
          ]),
        ),
      ),
    );
  }
}

/// A few shimmering rows, for a part of a screen that loads on its own (a list inside a page).
class SkeletonRows extends StatelessWidget {
  const SkeletonRows({super.key, this.rows = 3, this.label = 'Loading'});
  final int rows;
  final String label;
  @override
  Widget build(BuildContext context) => Semantics(
        label: label,
        liveRegion: true,
        child: ExcludeSemantics(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(children: [for (var i = 0; i < rows; i++) Padding(padding: const EdgeInsets.only(bottom: 12), child: Skeleton(height: 64, radius: Radii.tile))]),
          ),
        ),
      );
}

/// Anything you can press gives a little under the finger.
class Pressable extends StatefulWidget {
  const Pressable({super.key, required this.child, required this.onTap, this.label, this.selected});
  final Widget child;
  final VoidCallback? onTap;
  final String? label;
  final bool? selected;
  @override
  State<Pressable> createState() => _PressableState();
}

class _PressableState extends State<Pressable> {
  bool down = false;
  void _set(bool v) {
    if (mounted && down != v) setState(() => down = v);
  }

  @override
  Widget build(BuildContext context) => Semantics(
        button: true,
        label: widget.label,
        selected: widget.selected,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: widget.onTap,
          onTapDown: (_) => _set(true),
          onTapUp: (_) => _set(false),
          onTapCancel: () => _set(false),
          child: AnimatedScale(scale: down && !reduceMotion(context) ? .97 : 1, duration: const Duration(milliseconds: 110), child: widget.child),
        ),
      );
}

/// A value from 0 to 1 that grows once when the widget appears (bars growing in a chart, a progress line filling). 1 straight away with reduced motion.
class Grow extends StatelessWidget {
  const Grow({super.key, required this.builder, this.duration = Motion.chart});
  final Widget Function(BuildContext, double) builder;
  final Duration duration;
  @override
  Widget build(BuildContext context) => reduceMotion(context)
      ? builder(context, 1)
      : TweenAnimationBuilder<double>(tween: Tween(begin: 0, end: 1), duration: duration, curve: Motion.ease, builder: (c, v, _) => builder(c, v));
}
