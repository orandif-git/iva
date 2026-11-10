import 'package:flutter/material.dart';

import '../theme.dart';

enum Level { ok, warn, bad, ai, mute }

class Pill extends StatelessWidget {
  const Pill(this.text, this.level, {super.key, this.icon});
  final String text;
  final Level level;
  final IconData? icon;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final (bg, fg) = switch (level) {
      Level.ok => (t.okSoft, t.okInk),
      Level.warn => (t.warnSoft, t.warnInk),
      Level.bad => (t.critSoft, t.crit),
      Level.ai => (t.aiSoft, t.ai),
      Level.mute => (t.surface2, t.fg2),
    };
    return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 13, color: fg), const SizedBox(width: 4)],
          Text(text, style: TextStyle(color: fg, fontWeight: FontWeight.w600, fontSize: 12)),
        ]));
  }
}

/// A rounded square with an icon, tinted by what it means.
class Badge2 extends StatelessWidget {
  const Badge2(this.icon, this.level, {super.key, this.size = 40});
  final IconData icon;
  final Level level;
  final double size;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final (bg, fg) = switch (level) {
      Level.ok => (t.okSoft, t.okInk),
      Level.warn => (t.warnSoft, t.warnInk),
      Level.bad => (t.critSoft, t.crit),
      Level.ai => (t.aiSoft, t.ai),
      Level.mute => (t.surface2, t.fg2),
    };
    return Container(width: size, height: size, decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(size * .32)), child: Icon(icon, color: fg, size: size * .5));
  }
}

class Section extends StatelessWidget {
  const Section({super.key, required this.title, this.subtitle, required this.child});
  final String title;
  final String? subtitle;
  final Widget child;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Card(
        child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
              if (subtitle != null) Text(subtitle!, style: TextStyle(color: t.fg2, fontSize: 13)),
              const SizedBox(height: 10),
              child,
            ])));
  }
}

String ago(int? s) {
  if (s == null) return 'no data yet';
  if (s < 60) return '$s seconds ago';
  if (s < 3600) return '${s ~/ 60} min ago';
  return '${s ~/ 3600} h ago';
}
