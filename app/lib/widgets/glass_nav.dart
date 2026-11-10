import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:remixicon/remixicon.dart';

import '../theme.dart';

class NavDef {
  final IconData icon, selected;
  final String label;
  final int badge;
  const NavDef(this.icon, this.selected, this.label, {this.badge = 0});
}

/// The four places, with their own icons: a line icon when idle, a solid one when chosen.
class NavIcons {
  static const home = NavDef(Remix.home_5_line, Remix.home_5_fill, 'Home');
  static const alerts = NavDef(Remix.notification_3_line, Remix.notification_3_fill, 'Alerts');
  static const cameras = NavDef(Remix.camera_lens_line, Remix.camera_lens_fill, 'Cameras'); // the lens, like the "o" of iVision
  static const reports = NavDef(Remix.bar_chart_box_line, Remix.bar_chart_box_fill, 'Reports');
}

/// A full-width, frosted tab bar like the iPhone's and WhatsApp's: a hairline on top, content showing softly through it, the chosen icon turns solid and
/// gives a small spring, and switching places is instant (each place keeps where you were).
class AppTabBar extends StatelessWidget {
  const AppTabBar({super.key, required this.items, required this.index, required this.onTap});
  final List<NavDef> items;
  final int index;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final dark = Theme.of(context).brightness == Brightness.dark;
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
        child: Container(
          decoration: BoxDecoration(color: t.surface.withValues(alpha: dark ? .78 : .84), border: Border(top: BorderSide(color: t.line.withValues(alpha: .9), width: .8))),
          child: SafeArea(
            top: false,
            child: Center(
              heightFactor: 1,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 560),
                child: SizedBox(
                  height: 56,
                  child: Row(children: [
                    for (var i = 0; i < items.length; i++)
                      Expanded(child: _Item(def: items[i], on: i == index, onTap: () {
                        HapticFeedback.selectionClick();
                        onTap(i);
                      })),
                  ]),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Item extends StatefulWidget {
  const _Item({required this.def, required this.on, required this.onTap});
  final NavDef def;
  final bool on;
  final VoidCallback onTap;
  @override
  State<_Item> createState() => _ItemState();
}

class _ItemState extends State<_Item> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 380));
  late final Animation<double> _scale = TweenSequence<double>([
    TweenSequenceItem(tween: Tween(begin: 1.0, end: 1.22).chain(CurveTween(curve: Curves.easeOut)), weight: 38),
    TweenSequenceItem(tween: Tween(begin: 1.22, end: .94).chain(CurveTween(curve: Curves.easeInOut)), weight: 30),
    TweenSequenceItem(tween: Tween(begin: .94, end: 1.0).chain(CurveTween(curve: Curves.easeOut)), weight: 32),
  ]).animate(_c);

  @override
  void didUpdateWidget(_Item old) {
    super.didUpdateWidget(old);
    if (widget.on && !old.on) _c.forward(from: 0);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final d = widget.def, on = widget.on;
    final color = on ? t.brand : t.fg2.withValues(alpha: .9);
    return Semantics(
      button: true,
      selected: on,
      label: d.badge > 0 ? '${d.label}, ${d.badge} open' : d.label,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          ScaleTransition(
            scale: _scale,
            child: Stack(clipBehavior: Clip.none, children: [
              Icon(on ? d.selected : d.icon, size: 27, color: color),
              if (d.badge > 0) Positioned(right: -11, top: -5, child: Container(padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1), constraints: const BoxConstraints(minWidth: 17), decoration: BoxDecoration(color: t.crit, borderRadius: BorderRadius.circular(9), border: Border.all(color: t.surface, width: 1.5)), child: Text(d.badge > 99 ? '99+' : '${d.badge}', textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w700, height: 1.2)))),
            ]),
          ),
          const SizedBox(height: 3),
          Text(d.label, style: TextStyle(fontSize: 10.5, height: 1.1, fontWeight: on ? FontWeight.w700 : FontWeight.w500, color: color)),
        ]),
      ),
    );
  }
}
