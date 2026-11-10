import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:remixicon/remixicon.dart';

import '../theme.dart';
import 'brand.dart';

class NavDef {
  final IconData icon, selected;
  final String label;
  final int badge;
  final Widget Function(bool on, Color color, double size)? custom;   // a drawn icon, where the icon set has no good one
  const NavDef(this.icon, this.selected, this.label, {this.badge = 0, this.custom});
  Widget glyph(bool on, Color color, double size) => custom != null ? custom!(on, color, size) : Icon(on ? selected : icon, size: size, color: color);
}

/// A CCTV camera: a box camera with a lens, on a wall bracket. Outline when idle, solid body when chosen.
class CctvIcon extends StatelessWidget {
  const CctvIcon({super.key, required this.on, required this.color, this.size = 26});
  final bool on;
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => SizedBox(width: size, height: size, child: CustomPaint(painter: _CctvPainter(on, color)));
}

class _CctvPainter extends CustomPainter {
  _CctvPainter(this.on, this.color);
  final bool on;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final k = size.width / 24;
    final line = Paint()..color = color..style = PaintingStyle.stroke..strokeWidth = 1.9 * k..strokeCap = StrokeCap.round..strokeJoin = StrokeJoin.round;
    final fill = Paint()..color = color..style = PaintingStyle.fill;
    // wall bracket: a post on the left with an arm reaching to the camera
    canvas.drawLine(Offset(3.2 * k, 3.5 * k), Offset(3.2 * k, 20.5 * k), line);
    canvas.drawLine(Offset(3.2 * k, 12.5 * k), Offset(7.4 * k, 11 * k), line);
    // the body: a rounded box, tilted slightly down to the right
    canvas.save();
    canvas.translate(14 * k, 9 * k);
    canvas.rotate(0.2);
    final body = RRect.fromRectAndRadius(Rect.fromCenter(center: Offset.zero, width: 14 * k, height: 7.4 * k), Radius.circular(2.4 * k));
    if (on) { canvas.drawRRect(body, fill); } else { canvas.drawRRect(body, line); }
    // the lens at the front end
    final lensC = Offset(4.4 * k, 0);
    if (on) {
      canvas.drawCircle(lensC, 1.7 * k, Paint()..color = Colors.white.withValues(alpha: .92));
    } else {
      canvas.drawCircle(lensC, 1.5 * k, line..strokeWidth = 1.6 * k);
    }
    // a small sun-shield line above the body
    canvas.drawLine(Offset(-5.5 * k, -4.9 * k), Offset(5.5 * k, -4.9 * k), line..strokeWidth = 1.9 * k);
    canvas.restore();
    // a little recording light
    canvas.drawCircle(Offset(19.6 * k, 4.3 * k), 1.1 * k, fill);
  }

  @override
  bool shouldRepaint(_CctvPainter o) => o.on != on || o.color != color;
}

/// An eye that watches, with the AI sparkle beside it: "AI Watch".
class AiEyeIcon extends StatelessWidget {
  const AiEyeIcon({super.key, required this.on, required this.color, this.size = 26});
  final bool on;
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: Stack(clipBehavior: Clip.none, children: [
          Positioned(left: 0, bottom: 0, child: Icon(on ? Remix.eye_fill : Remix.eye_line, size: size * .92, color: color)),
          Positioned(right: -size * .16, top: -size * .12, child: AiSpark(size: size * .52)),
        ]),
      );
}

Widget _cctv(bool on, Color c, double s) => CctvIcon(on: on, color: c, size: s);
Widget _aiEye(bool on, Color c, double s) => AiEyeIcon(on: on, color: c, size: s);

/// The five places, with their own icons: a line icon when idle, a solid one when chosen.
class NavIcons {
  static const home = NavDef(Remix.home_5_line, Remix.home_5_fill, 'Home');
  static const alerts = NavDef(Remix.notification_3_line, Remix.notification_3_fill, 'Alerts');
  static const cameras = NavDef(Remix.webcam_line, Remix.webcam_fill, 'Cameras', custom: _cctv);       // a CCTV camera on a wall bracket
  static const aiWatch = NavDef(Remix.eye_line, Remix.eye_fill, 'AI Watch', custom: _aiEye);        // an eye with the AI sparkle
  static const more = NavDef(Remix.apps_2_line, Remix.apps_2_fill, 'More');                  // Reports and Settings
}

/// The floating, frosted bar: a rounded pill that hovers above the page, with the content showing softly through it. A highlight slides to the chosen place,
/// the chosen icon turns solid and gives a small spring, and switching places is instant (each place keeps where you were).
class AppTabBar extends StatelessWidget {
  const AppTabBar({super.key, required this.items, required this.index, required this.onTap});
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
                filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
                child: Container(
                  height: 70,
                  decoration: BoxDecoration(
                    color: t.surface.withValues(alpha: dark ? .74 : .8),
                    borderRadius: BorderRadius.circular(34),
                    border: Border.all(color: t.line.withValues(alpha: .9)),
                    boxShadow: [BoxShadow(color: t.shadow.withValues(alpha: .26), blurRadius: 26, offset: const Offset(0, 10))],
                  ),
                  child: LayoutBuilder(builder: (context, box) {
                    final w = (box.maxWidth - 12) / items.length;
                    return Stack(children: [
                      AnimatedPositioned(
                        duration: const Duration(milliseconds: 320),
                        curve: Curves.easeOutBack,
                        left: 6 + index * w,
                        top: 7,
                        width: w,
                        height: 56,
                        child: Container(margin: const EdgeInsets.symmetric(horizontal: 3), decoration: BoxDecoration(color: t.brandSoft, borderRadius: BorderRadius.circular(28))),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 6),
                        child: Row(children: [
                          for (var i = 0; i < items.length; i++)
                            Expanded(child: _Item(def: items[i], on: i == index, onTap: () {
                              HapticFeedback.selectionClick();
                              onTap(i);
                            })),
                        ]),
                      ),
                    ]);
                  }),
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
        child: SizedBox(height: 70, child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          ScaleTransition(
            scale: _scale,
            child: Stack(clipBehavior: Clip.none, children: [
              d.glyph(on, color, 27),
              if (d.badge > 0) Positioned(right: -11, top: -5, child: Container(padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1), constraints: const BoxConstraints(minWidth: 17), decoration: BoxDecoration(color: t.crit, borderRadius: BorderRadius.circular(9), border: Border.all(color: t.surface, width: 1.5)), child: Text(d.badge > 99 ? '99+' : '${d.badge}', textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w700, height: 1.2)))),
            ]),
          ),
          const SizedBox(height: 3),
          // One line always: on a narrow phone or a wide font the label shrinks a little instead of wrapping into its neighbour.
          Padding(padding: const EdgeInsets.symmetric(horizontal: 2), child: FittedBox(fit: BoxFit.scaleDown, child: Text(d.label, maxLines: 1, softWrap: false, style: TextStyle(fontSize: 10.5, height: 1.1, fontWeight: on ? FontWeight.w700 : FontWeight.w500, color: color)))),
        ])),
      ),
    );
  }
}
