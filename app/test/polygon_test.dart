import 'package:flutter_test/flutter_test.dart';
import 'package:ivision_app/geometry/polygon.dart';

void main() {
  const square = [Pt(.1, .1), Pt(.6, .1), Pt(.6, .6), Pt(.1, .6)];
  const lShape = [Pt(.1, .1), Pt(.6, .1), Pt(.6, .3), Pt(.3, .3), Pt(.3, .7), Pt(.1, .7)];

  test('a square and an L-shape are fine', () {
    expect(checkShape(square), isNull);
    expect(checkShape(lShape), isNull);
  });

  test('the same shapes the server refuses are refused here', () {
    expect(checkShape(const [Pt(.1, .1), Pt(.6, .6), Pt(.6, .1), Pt(.1, .6)]), contains('cross'), reason: 'bow-tie');
    expect(checkShape(const [Pt(.1, .1), Pt(.1, .1), Pt(.6, .6)]), contains('same place'));
    expect(checkShape(const [Pt(.1, .1), Pt(.5, .1), Pt(.5, .12)]), contains('small'), reason: 'sliver');
    expect(checkShape(const [Pt(.1, .1), Pt(.3, .3), Pt(.5, .5)]), contains('small'), reason: 'all in a line');
    expect(checkShape(square.sublist(0, 2)), contains('3 corners'));
  });

  test('twelve corners is the most', () {
    final twelve = [for (var i = 0; i < 12; i++) Pt(.5 + .4 * _c(i / 12), .5 + .4 * _s(i / 12))];
    expect(checkShape(twelve), isNull);
    expect(checkShape([...twelve, const Pt(.5, .5)]), contains('12'));
  });

  test('inside and outside, for any shape', () {
    expect(contains_(square, const Pt(.3, .3)), isTrue);
    expect(contains_(square, const Pt(.8, .3)), isFalse);
    expect(contains_(lShape, const Pt(.2, .6)), isTrue, reason: 'the long arm');
    expect(contains_(lShape, const Pt(.5, .5)), isFalse, reason: 'the notch of the L');
  });
}

double _c(double t) => _cos(t * 6.283185307);
double _s(double t) => _sin(t * 6.283185307);
bool contains_(List<Pt> poly, Pt p) => pointInPolygon(poly, p);

// tiny trig so the test needs no imports beyond the app
double _cos(double x) => _sin(x + 1.5707963268);
double _sin(double x) {
  x = x % 6.283185307;
  if (x > 3.14159265) x -= 6.283185307;
  final x3 = x * x * x, x5 = x3 * x * x, x7 = x5 * x * x, x9 = x7 * x * x;
  return x - x3 / 6 + x5 / 120 - x7 / 5040 + x9 / 362880;
}
