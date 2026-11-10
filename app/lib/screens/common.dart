import 'package:flutter/material.dart';

import '../theme.dart';
import '../widgets/brand.dart';

enum Level { ok, warn, bad, ai, mute }

(Color, Color) levelColors(Tok t, Level l) => switch (l) {
      Level.ok => (t.okSoft, t.okInk),
      Level.warn => (t.warnSoft, t.warnInk),
      Level.bad => (t.critSoft, t.crit),
      Level.ai => (t.aiSoft, t.ai),
      Level.mute => (t.surface2, t.fg2),
    };

class Pill extends StatelessWidget {
  const Pill(this.text, this.level, {super.key, this.icon, this.dot = false});
  final String text;
  final Level level;
  final IconData? icon;
  final bool dot;
  @override
  Widget build(BuildContext context) {
    final (bg, fg) = levelColors(Tok.of(context), level);
    return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (dot) ...[Container(width: 7, height: 7, decoration: BoxDecoration(color: fg, shape: BoxShape.circle)), const SizedBox(width: 6)],
          if (icon != null) ...[Icon(icon, size: 13, color: fg), const SizedBox(width: 4)],
          Text(text, style: TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 12)),
        ]));
  }
}

/// A rounded square with an icon, tinted by what it means. [ai] adds the AI sparkle in the corner: every Smart Alert carries it.
class Badge2 extends StatelessWidget {
  const Badge2(this.icon, this.level, {super.key, this.size = 44, this.ai = false});
  final IconData icon;
  final Level level;
  final double size;
  final bool ai;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final (bg, fg) = levelColors(t, ai ? Level.ai : level);
    return SizedBox(
      width: size + (ai ? 6 : 0),
      height: size + (ai ? 6 : 0),
      child: Stack(clipBehavior: Clip.none, children: [
        Positioned(left: 0, bottom: 0, child: Container(width: size, height: size, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(size * .32)), child: Icon(icon, color: fg, size: size * .48))),
        if (ai) Positioned(right: 0, top: 0, child: Container(padding: const EdgeInsets.all(3), decoration: BoxDecoration(color: t.surface, shape: BoxShape.circle, boxShadow: [BoxShadow(color: t.shadow, blurRadius: 4)]), child: AiSpark(size: size * .3))),
      ]),
    );
  }
}

class Section extends StatelessWidget {
  const Section({super.key, required this.title, this.subtitle, required this.child, this.trailing, this.icon});
  final String title;
  final String? subtitle;
  final Widget child;
  final Widget? trailing;
  final IconData? icon;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
        decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: t.line), boxShadow: t.lift),
        padding: const EdgeInsets.all(20),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            if (icon != null) ...[Icon(icon, size: 20, color: t.brand), const SizedBox(width: 8)],
            Expanded(child: Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700, letterSpacing: -.2))),
            if (trailing != null) trailing!,
          ]),
          if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 2), child: Text(subtitle!, style: TextStyle(color: t.fg2, fontSize: 13))),
          const SizedBox(height: 12),
          child,
        ]));
  }
}

/// A friendly empty state: a soft badge, a headline and a line of help.
class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, required this.text, this.action});
  final IconData icon;
  final String title, text;
  final Widget? action;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
        padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(width: 64, height: 64, decoration: BoxDecoration(color: t.brandSoft, shape: BoxShape.circle), child: Icon(icon, color: t.brand, size: 30)),
          const SizedBox(height: 14),
          Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700), textAlign: TextAlign.center),
          const SizedBox(height: 4),
          Text(text, style: TextStyle(color: t.fg2), textAlign: TextAlign.center),
          if (action != null) ...[const SizedBox(height: 14), action!],
        ]));
  }
}

String ago(int? s) {
  if (s == null) return 'no data yet';
  if (s < 60) return '$s seconds ago';
  if (s < 3600) return '${s ~/ 60} min ago';
  return '${s ~/ 3600} h ago';
}
