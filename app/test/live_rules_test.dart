import 'package:flutter_test/flutter_test.dart';
import 'package:ivision_app/live/live_rules.dart';

void main() {
  test('live view lasts 2 minutes and AI view 30 seconds', () {
    expect(LiveClock(LiveMode.live).left, 120);
    expect(LiveClock(LiveMode.ai).left, 30);
  });

  test('it closes by itself when the time is up', () {
    final c = LiveClock(LiveMode.ai);
    for (var i = 0; i < 30; i++) {
      c.tick();
    }
    expect(c.ended, isTrue);
    expect(c.canExtend, isFalse);
    expect(c.extend(), isFalse);
  });

  test('AI view can be extended by 30 s until 2 minutes in all', () {
    final c = LiveClock(LiveMode.ai);
    for (var i = 0; i < 3; i++) {
      expect(c.extend(), isTrue);
    }
    expect(c.left, 120);
    expect(c.canExtend, isFalse);
    expect(c.extend(), isFalse);
  });

  test('live view can be extended by 2 minutes until 10 minutes in all', () {
    final c = LiveClock(LiveMode.live);
    var n = 0;
    while (c.extend()) {
      n++;
    }
    expect(n, 4);
    expect(c.left, 600);
  });

  test('time already watched counts toward the limit', () {
    final c = LiveClock(LiveMode.ai);
    for (var i = 0; i < 20; i++) {
      c.tick();
    }
    expect(c.extend(), isTrue); // 10 left + 30 = 40, elapsed 20: 60 of 120
    expect(c.left, 40);
    for (var i = 0; i < 40; i++) {
      c.tick();
    }
    expect(c.ended, isTrue);
  });

  test('stop ends it at once, and the first seconds are "connecting"', () {
    final c = LiveClock(LiveMode.live);
    expect(c.connecting, isTrue);
    c.tick();
    c.tick();
    expect(c.connecting, isFalse);
    c.stop();
    expect(c.ended, isTrue);
  });

  test('the countdown reads like a clock', () {
    final c = LiveClock(LiveMode.live);
    expect(c.countdown, '2:00');
    c.tick();
    expect(c.countdown, '1:59');
  });
}
