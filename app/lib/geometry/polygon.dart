/// A drawn area. Corners are stored as 0 to 1 of the picture, so they survive a change of picture size. The same rules as the server
/// (backend `check_shape`): 3 to 12 corners, no corners on top of each other, no crossing edges, not a sliver.
class Pt {
  final double x, y;
  const Pt(this.x, this.y);
  Pt copyWith({double? x, double? y}) => Pt(x ?? this.x, y ?? this.y);
  List<double> toJson() => [x, y];
  @override
  bool operator ==(Object other) => other is Pt && other.x == x && other.y == y;
  @override
  int get hashCode => Object.hash(x, y);
}

const maxCorners = 12;
const minArea = 0.005;

double _ccw(Pt a, Pt b, Pt c) => (c.y - a.y) * (b.x - a.x) - (b.y - a.y) * (c.x - a.x);

double area(List<Pt> p) {
  var s = 0.0;
  for (var i = 0; i < p.length; i++) {
    final j = (i + 1) % p.length;
    s += p[i].x * p[j].y - p[j].x * p[i].y;
  }
  return s.abs() / 2;
}

/// Null when the shape is fine, otherwise a plain-words reason.
String? checkShape(List<Pt> p) {
  if (p.length < 3) return 'An area needs at least 3 corners.';
  if (p.length > maxCorners) return 'An area can have at most $maxCorners corners.';
  for (var i = 0; i < p.length; i++) {
    for (var j = i + 1; j < p.length; j++) {
      if ((p[i].x - p[j].x).abs() < 1e-4 && (p[i].y - p[j].y).abs() < 1e-4) return 'Two corners are in the same place.';
    }
  }
  final n = p.length;
  for (var i = 0; i < n; i++) {
    for (var j = i + 1; j < n; j++) {
      if (j == i + 1 || (i == 0 && j == n - 1)) continue; // edges that share a corner may touch there
      final a = p[i], b = p[(i + 1) % n], c = p[j], d = p[(j + 1) % n];
      if (_ccw(a, b, c) * _ccw(a, b, d) < 0 && _ccw(c, d, a) * _ccw(c, d, b) < 0) return 'The edges of the area cross each other.';
    }
  }
  if (area(p) < minArea) return 'The area is too small.';
  return null;
}

/// Is the point inside the shape? (Same test the worker uses to count people.)
bool pointInPolygon(List<Pt> poly, Pt p) {
  var inside = false;
  for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    final a = poly[i], b = poly[j];
    if ((a.y > p.y) != (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}
