import 'package:flutter/material.dart';

import '../theme.dart';
import '../widgets/brand.dart';
import '../widgets/motion.dart';

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
          Flexible(child: Text(text, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 12))),
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
        decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
        padding: EdgeInsets.all(cardPad(context)),
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
          Text(title, style: display(18), textAlign: TextAlign.center),
          const SizedBox(height: 4),
          Text(text, style: TextStyle(color: t.fg2), textAlign: TextAlign.center),
          if (action != null) ...[const SizedBox(height: 14), action!],
        ]));
  }
}

String ago(int? s) {
  if (s == null) return 'no data yet';
  if (s < 5) return 'just now';
  if (s < 60) return '$s seconds ago';
  if (s < 3600) return '${s ~/ 60} min ago';
  return '${s ~/ 3600} h ago';
}

/// "27 s ago", "5 min ago": short enough for a small card.
String shortAgo(int? s) => s == null ? '' : (s < 5 ? 'just now' : (s < 60 ? '$s s ago' : (s < 3600 ? '${s ~/ 60} min ago' : '${s ~/ 3600} h ago')));

/// The icon of an alert by its kind. Smart Alerts get their own picture (people, queue, phone), not a sparkle: the sparkle is the badge's corner.
IconData iconForType(String type, {bool smart = false}) => switch (type) {
      'camera_offline' => Icons.videocam_off_outlined,
      'recorder_offline' => Icons.wifi_off_rounded,
      'connection_unstable' => Icons.sync_problem_outlined,
      'people_in_area' => Icons.groups_outlined,
      'queue_long' => Icons.format_list_numbered_rounded,
      'phone_use' => Icons.smartphone_rounded,
      'blocked_plate' => Icons.directions_car_outlined,
      'crowding' => Icons.groups_2_outlined,
      'after_hours' => Icons.nightlight_outlined,
      'vehicle_overstay' => Icons.timer_outlined,
      'low_confidence_plate' => Icons.plagiarism_outlined,
      'daily_summary' => Icons.summarize_outlined,
      'back_to_normal' => Icons.check_circle_outline_rounded,
      _ => smart ? Icons.remove_red_eye_outlined : Icons.notifications_none_rounded,
    };

/// Spacing that tightens on a phone.
double cardPad(BuildContext c) => MediaQuery.sizeOf(c).width < 600 ? 16 : 20;

/// Room at the bottom of a scrolling page so the floating tab bar never covers the last row.
const navClear = 104.0;

/// One rounded surface with hairline separators between its rows (like a settings list). Fewer boxes, calmer pages.
class Group extends StatelessWidget {
  const Group({super.key, required this.children, this.padding = EdgeInsets.zero, this.bare = false});
  final List<Widget> children;
  final EdgeInsets padding;
  final bool bare; // inside a card already: no second border
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final rows = <Widget>[];
    for (var i = 0; i < children.length; i++) {
      if (i > 0) rows.add(Divider(height: 1, thickness: 1, indent: bare ? 0 : 16, endIndent: 0, color: t.line));
      rows.add(children[i]);
    }
    return Container(
      decoration: bare ? null : BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
      clipBehavior: bare ? Clip.none : Clip.antiAlias,
      padding: padding,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: rows),
    );
  }
}

/// A small uppercase label above a group.
class GroupLabel extends StatelessWidget {
  const GroupLabel(this.text, {super.key, this.trailing});
  final String text;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(6, 22, 6, 8),
      child: Row(children: [
        Expanded(child: Semantics(header: true, child: Text(text.toUpperCase(), style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, letterSpacing: .6, color: t.fg2)))),
        if (trailing != null) trailing!,
      ]),
    );
  }
}

/// The name of a screen at the top of its page: Space Grotesk, 26. One per screen, with an optional line under it and an action on the right.
class PageTitle extends StatelessWidget {
  const PageTitle(this.text, {super.key, this.subtitle, this.trailing});
  final String text;
  final String? subtitle;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Expanded(child: Semantics(header: true, child: Text(text, style: display(26)))),
        if (trailing != null) trailing!,
      ]),
      if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 6), child: Text(subtitle!, style: TextStyle(color: t.fg2, fontSize: 14))),
    ]);
  }
}

/// The answer in one soft pill with a dot: "All 8 cameras are working".
class StatusPill extends StatelessWidget {
  const StatusPill(this.text, this.level, {super.key});
  final String text;
  final Level level;
  @override
  Widget build(BuildContext context) {
    final (bg, fg) = levelColors(Tok.of(context), level);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(16)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(color: fg, shape: BoxShape.circle)),
        const SizedBox(width: 8),
        Flexible(child: Text(text, style: TextStyle(color: fg, fontWeight: FontWeight.w600, fontSize: 13))),
      ]),
    );
  }
}

/// Figures side by side in one card: a small label above a big number, with hairlines between them.
class StatStrip extends StatelessWidget {
  const StatStrip(this.items, {super.key});
  final List<(String label, String value)> items;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      decoration: BoxDecoration(color: t.surface, borderRadius: BorderRadius.circular(Radii.card), border: Border.all(color: t.line)),
      child: IntrinsicHeight(
        child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          for (var i = 0; i < items.length; i++) ...[
            if (i > 0) VerticalDivider(width: 1, thickness: 1, color: t.line),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 16, 10, 16),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(items[i].$1, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: t.fg2, fontSize: 12.5)),
                  const SizedBox(height: 4),
                  FittedBox(fit: BoxFit.scaleDown, alignment: Alignment.centerLeft, child: Text(items[i].$2, style: display(26))),
                ]),
              ),
            ),
          ],
        ]),
      ),
    );
  }
}

/// A choice between a few things, as one soft bar with a white pill on the chosen one (People · Vehicles · Areas · Health).
class Segments<T> extends StatelessWidget {
  const Segments({super.key, required this.items, required this.value, required this.onChanged});
  final List<(T, String)> items;
  final T value;
  final ValueChanged<T> onChanged;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(color: t.surface2, borderRadius: BorderRadius.circular(Radii.button)),
      child: Row(children: [
        for (final (v, label) in items)
          Expanded(
            child: Pressable(
              label: label,
              selected: v == value,
              onTap: () => onChanged(v),
              child: ExcludeSemantics(
                child: AnimatedContainer(
                  duration: reduceMotion(context) ? Duration.zero : Motion.quick,
                  height: 44,
                  alignment: Alignment.center,
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  decoration: BoxDecoration(color: v == value ? t.surface : Colors.transparent, borderRadius: BorderRadius.circular(12), boxShadow: v == value ? [BoxShadow(color: t.shadow.withValues(alpha: .08), blurRadius: 4, offset: const Offset(0, 1))] : null),
                  child: FittedBox(fit: BoxFit.scaleDown, child: Text(label, maxLines: 1, style: TextStyle(fontSize: 13.5, fontWeight: v == value ? FontWeight.w700 : FontWeight.w500, color: t.fg))),
                ),
              ),
            ),
          ),
      ]),
    );
  }
}
