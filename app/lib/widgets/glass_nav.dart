import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';

class NavDef {
  final IconData icon, selected;
  final String label;
  final int badge;
  const NavDef(this.icon, this.selected, this.label, {this.badge = 0});
}

/// A floating, frosted pill with the main places, like the service-desk app. It lifts off the page so content shows through the blur.
class GlassNav extends StatelessWidget {
  const GlassNav({super.key, required this.items, required this.index, required this.onTap});
  final List<NavDef> items;
  final int index;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final dark = Theme.of(context).brightness == Brightness.dark;
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        child: Center(
          heightFactor: 1,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(34),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                child: Container(
                  height: 70,
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  decoration: BoxDecoration(
                    color: t.surface.withValues(alpha: dark ? .78 : .82),
                    borderRadius: BorderRadius.circular(34),
                    border: Border.all(color: t.line.withValues(alpha: .9)),
                    boxShadow: [BoxShadow(color: t.shadow.withValues(alpha: .22), blurRadius: 24, offset: const Offset(0, 8))],
                  ),
                  child: Row(children: [for (var i = 0; i < items.length; i++) Expanded(child: _Item(items[i], i == index, () { HapticFeedback.selectionClick(); onTap(i); }))]),
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
  const _Item(this.def, this.on, this.onTap);
  final NavDef def;
  final bool on;
  final VoidCallback onTap;
  @override
  State<_Item> createState() => _ItemState();
}

class _ItemState extends State<_Item> {
  bool down = false;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final d = widget.def, on = widget.on;
    return Semantics(
      button: true,
      selected: on,
      label: d.badge > 0 ? '${d.label}, ${d.badge} open' : d.label,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (_) => setState(() => down = true),
        onTapCancel: () => setState(() => down = false),
        onTapUp: (_) => setState(() => down = false),
        onTap: widget.onTap,
        child: AnimatedScale(
          scale: down ? .93 : 1,
          duration: const Duration(milliseconds: 110),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
            margin: const EdgeInsets.symmetric(vertical: 6, horizontal: 3),
            decoration: BoxDecoration(color: on ? t.brandSoft : Colors.transparent, borderRadius: BorderRadius.circular(26)),
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Stack(clipBehavior: Clip.none, children: [
                Icon(on ? d.selected : d.icon, size: 24, color: on ? t.brand : t.fg2),
                if (d.badge > 0) Positioned(right: -9, top: -5, child: Container(padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1), constraints: const BoxConstraints(minWidth: 16), decoration: BoxDecoration(color: t.crit, borderRadius: BorderRadius.circular(9)), child: Text(d.badge > 99 ? '99+' : '${d.badge}', textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w700, height: 1.25)))),
              ]),
              const SizedBox(height: 2),
              Text(d.label, style: TextStyle(fontSize: 11.5, height: 1.1, fontWeight: on ? FontWeight.w700 : FontWeight.w600, color: on ? t.brand : t.fg2)),
            ]),
          ),
        ),
      ),
    );
  }
}
