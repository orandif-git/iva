import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../geometry/polygon.dart';
import '../theme.dart';

double _round3(double v) => (v.clamp(0.0, 1.0) * 1000).round() / 1000;

/// The counting line: two points across a doorway, and which way is "in". Positions are fractions of the picture (0 to 1), as the server stores them.
class CountLineController extends ChangeNotifier {
  CountLineController({Pt? a, Pt? b, int flip = 1})
      : a = a ?? const Pt(0.15, 0.6),
        b = b ?? const Pt(0.85, 0.6),
        flip = flip >= 0 ? 1 : -1;

  Pt a, b;
  int flip;
  bool dirty = false;

  /// The direction at right angles to the line, as the server expects it (before "swap").
  Pt get normal {
    final dx = b.x - a.x, dy = b.y - a.y, n = math.sqrt(dx * dx + dy * dy);
    final len = n == 0 ? 1.0 : n;
    return Pt(-dy / len, dx / len);
  }

  /// Which way people cross to count as "in".
  Pt get inDirection => Pt((normal.x * flip * 1000).round() / 1000, (normal.y * flip * 1000).round() / 1000);

  /// The line as the app got it from the server: [inDirection] decides which side is "in".
  static int flipFor(List<Pt> line, Pt? inDirection) {
    if (inDirection == null || line.length != 2) return 1;
    final dx = line[1].x - line[0].x, dy = line[1].y - line[0].y, n = math.sqrt(dx * dx + dy * dy);
    final len = n == 0 ? 1.0 : n;
    final dot = inDirection.x * (-dy / len) + inDirection.y * (dx / len);
    return dot < 0 ? -1 : 1;
  }

  void move(int i, Pt p) {
    if (i == 0) {
      a = p;
    } else {
      b = p;
    }
    dirty = true;
    notifyListeners();
  }

  void swap() {
    flip = -flip;
    dirty = true;
    notifyListeners();
  }
}

/// The camera's picture with the counting line over it: drag the two dots to put the line across the doorway; the arrow shows which way counts as "in".
class CountLineEditor extends StatelessWidget {
  const CountLineEditor({super.key, required this.controller, this.picture});
  final CountLineController controller;
  final Widget? picture;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return SelectionContainer.disabled(
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14),
        child: AspectRatio(
          aspectRatio: 16 / 9,
          child: ListenableBuilder(
            listenable: controller,
            builder: (context, _) => LayoutBuilder(builder: (context, box) {
              final w = box.maxWidth, h = box.maxHeight;
              Offset px(Pt p) => Offset(p.x * w, p.y * h);
              Pt norm(Offset o) => Pt(_round3(o.dx / w), _round3(o.dy / h));
              Widget handle(int i, Pt p) => Positioned(
                    left: px(p).dx - 24,
                    top: px(p).dy - 24,
                    width: 48,
                    height: 48,
                    child: Semantics(
                      label: 'Line end ${i + 1}, drag to move',
                      child: GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onPanUpdate: (d) => controller.move(i, norm(px(p) + d.delta)),
                        child: Center(child: Container(width: 26, height: 26, decoration: BoxDecoration(shape: BoxShape.circle, color: const Color(0xFFFFD23F), border: Border.all(color: const Color(0xFF0A1218), width: 3)))),
                      ),
                    ),
                  );
              return Stack(children: [
                Positioned.fill(child: picture ?? const DecoratedBox(decoration: BoxDecoration(gradient: LinearGradient(colors: [Color(0xFF243449), Color(0xFF3A5272)])))),
                Positioned.fill(child: CustomPaint(painter: _LinePainter(controller.a, controller.b, controller.normal, controller.flip, t.brand))),
                handle(0, controller.a),
                handle(1, controller.b),
              ]);
            }),
          ),
        ),
      ),
    );
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter(this.a, this.b, this.normal, this.flip, this.brand);
  final Pt a, b, normal;
  final int flip;
  final Color brand;

  @override
  void paint(Canvas canvas, Size size) {
    Offset px(double x, double y) => Offset(x * size.width, y * size.height);
    final pa = px(a.x, a.y), pb = px(b.x, b.y);
    canvas.drawLine(pa, pb, Paint()..color = const Color(0xFFFFD23F)..strokeWidth = 4..strokeCap = StrokeCap.round);
    // the arrow: from the middle of the line, the way people crossing count as "in"
    final mid = Offset((pa.dx + pb.dx) / 2, (pa.dy + pb.dy) / 2);
    final dx = (b.x - a.x) * size.width, dy = (b.y - a.y) * size.height, len = math.sqrt(dx * dx + dy * dy);
    if (len == 0) return;
    final n = Offset(-dy / len * flip, dx / len * flip);
    final tip = mid + n * 46;
    final arrow = Paint()..color = Colors.white..strokeWidth = 3.5..strokeCap = StrokeCap.round..style = PaintingStyle.stroke;
    canvas.drawLine(mid, tip, arrow);
    final side = Offset(-n.dy, n.dx);
    canvas.drawLine(tip, tip - n * 11 + side * 8, arrow);
    canvas.drawLine(tip, tip - n * 11 - side * 8, arrow);
    final label = TextPainter(text: const TextSpan(text: 'IN', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w800)), textDirection: TextDirection.ltr)..layout();
    label.paint(canvas, tip + n * 6 - Offset(label.width / 2, label.height / 2));
  }

  @override
  bool shouldRepaint(_LinePainter o) => o.a != a || o.b != b || o.flip != flip;
}
