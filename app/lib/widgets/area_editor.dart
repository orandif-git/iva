import 'package:flutter/material.dart';

import '../geometry/polygon.dart';
import '../theme.dart';

/// The shape being drawn. Tap the picture to add corners, tap the green first corner to close, drag any corner to reshape, Undo steps back, Redraw starts again.
class AreaController extends ChangeNotifier {
  AreaController({List<Pt>? points, bool closed = false})
      : _points = List.of(points ?? const []),
        _closed = closed;

  List<Pt> _points;
  bool _closed;
  List<Pt> get points => List.unmodifiable(_points);
  bool get closed => _closed;
  bool get isEmpty => _points.isEmpty;
  bool get canClose => !_closed && _points.length >= 3 && checkShape(_points) == null;

  /// What is wrong with the shape right now, once it is closed.
  String? get problem => _closed ? checkShape(_points) : null;
  bool get valid => _closed && checkShape(_points) == null;

  void add(Pt p) {
    if (_closed || _points.length >= maxCorners) return;
    _points.add(p);
    notifyListeners();
  }

  void move(int i, Pt p) {
    if (i < 0 || i >= _points.length) return;
    _points[i] = p;
    notifyListeners();
  }

  bool close() {
    if (!canClose) return false;
    _closed = true;
    notifyListeners();
    return true;
  }

  void undo() {
    if (_points.isEmpty) return;
    _points.removeLast();
    _closed = false;
    notifyListeners();
  }

  void redraw() {
    _points = [];
    _closed = false;
    notifyListeners();
  }
}

class AreaEditor extends StatelessWidget {
  const AreaEditor({super.key, required this.controller, this.picture, this.label = 'Camera picture'});
  final AreaController controller;
  final Widget? picture;
  final String label;

  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(_help(), style: TextStyle(color: t.fg2)),
        const SizedBox(height: 8),
        SelectionContainer.disabled(child: ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: AspectRatio(
            aspectRatio: 16 / 9,
            child: LayoutBuilder(builder: (context, box) {
              final w = box.maxWidth, h = box.maxHeight;
              Offset px(Pt p) => Offset(p.x * w, p.y * h);
              Pt norm(Offset o) => Pt((o.dx / w).clamp(0.0, 1.0), (o.dy / h).clamp(0.0, 1.0));
              return Semantics(
                label: '$label. Tap to add corners of the area.',
                child: Stack(children: [
                  Positioned.fill(child: picture ?? const DecoratedBox(decoration: BoxDecoration(gradient: LinearGradient(colors: [Color(0xFF243449), Color(0xFF3A5272)])))),
                  Positioned.fill(
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onTapUp: (d) => controller.add(norm(d.localPosition)),
                      child: CustomPaint(painter: _ShapePainter(controller.points, controller.closed)),
                    ),
                  ),
                  for (var i = 0; i < controller.points.length; i++)
                    Positioned(
                      left: px(controller.points[i]).dx - 22,
                      top: px(controller.points[i]).dy - 22,
                      width: 44,
                      height: 44,
                      child: Semantics(
                        button: true,
                        label: 'Corner ${i + 1}${i == 0 && controller.canClose ? ', tap to close the shape' : ', drag to move'}',
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onTap: () {
                            if (i == 0) controller.close();
                          },
                          onPanUpdate: (d) => controller.move(i, norm(px(controller.points[i]) + d.delta)),
                          child: Center(
                            child: Container(
                              width: 26,
                              height: 26,
                              decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: i == 0 && !controller.closed ? const Color(0xFF0B6B3A) : t.brand,
                                  border: Border.all(color: Colors.white, width: 3),
                                  boxShadow: const [BoxShadow(color: Colors.black38, blurRadius: 4)]),
                            ),
                          ),
                        ),
                      ),
                    ),
                ]),
              );
            }),
          ),
        )),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          OutlinedButton(onPressed: controller.isEmpty ? null : controller.undo, child: const Text('Undo')),
          OutlinedButton(onPressed: controller.canClose ? controller.close : null, child: const Text('Close the shape')),
          OutlinedButton(onPressed: controller.isEmpty ? null : controller.redraw, child: const Text('Redraw')),
          _Status(controller),
        ]),
      ]),
    );
  }

  String _help() {
    final n = controller.points.length;
    if (controller.closed) return controller.problem ?? 'Drag a corner to reshape the area. Redraw starts again.';
    if (n >= 3) return 'Keep tapping to add corners, or tap the green corner to close the shape.';
    return 'Tap the picture to place the first corner, then go around the place you want to watch.';
  }
}

class _Status extends StatelessWidget {
  const _Status(this.c);
  final AreaController c;
  @override
  Widget build(BuildContext context) {
    final t = Tok.of(context);
    final bad = c.problem != null;
    final text = c.closed ? (bad ? c.problem! : 'Area set · ${c.points.length} corners') : (c.points.isEmpty ? 'Not drawn yet' : '${c.points.length} corners so far');
    final bg = c.closed ? (bad ? t.critSoft : t.okSoft) : t.surface2;
    final fg = c.closed ? (bad ? t.crit : t.okInk) : t.fg2;
    return Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4), decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)), child: Text(text, style: TextStyle(color: fg, fontWeight: FontWeight.w600, fontSize: 12)));
  }
}

class _ShapePainter extends CustomPainter {
  _ShapePainter(this.pts, this.closed);
  final List<Pt> pts;
  final bool closed;
  @override
  void paint(Canvas canvas, Size s) {
    if (pts.length < 2) return;
    final path = Path()..moveTo(pts[0].x * s.width, pts[0].y * s.height);
    for (final p in pts.skip(1)) {
      path.lineTo(p.x * s.width, p.y * s.height);
    }
    const cyan = Color(0xFF6BE0FF);
    if (closed) {
      path.close();
      canvas.drawPath(path, Paint()..color = cyan.withValues(alpha: .22));
      canvas.drawPath(path, Paint()..color = cyan..style = PaintingStyle.stroke..strokeWidth = 2.5..strokeJoin = StrokeJoin.round);
    } else {
      canvas.drawPath(path, Paint()..color = cyan..style = PaintingStyle.stroke..strokeWidth = 2);
    }
  }

  @override
  bool shouldRepaint(_ShapePainter o) => true;
}
