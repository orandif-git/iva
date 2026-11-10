import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ivision_app/live/live_view.dart';
import 'package:ivision_app/models.dart';
import 'package:ivision_app/repo.dart';

class Spy extends MockRepository {
  final calls = <String>[];
  @override
  Future<LiveSession> startLive(String id, mode) {
    calls.add('start ${mode.name}');
    return super.startLive(id, mode);
  }

  @override
  Future<void> extendLive(LiveSession s) async => calls.add('extend');
  @override
  Future<void> stopLive(LiveSession s) async => calls.add('stop');
}

Widget host(Spy repo, {bool working = true}) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: LiveView(repo: repo, camera: CameraItem('c1', 'Front door', 'Store recorder', working), compact: true))));

void tall(WidgetTester t) {
  t.view.physicalSize = const Size(390 * 2, 1600 * 2);
  t.view.devicePixelRatio = 2;
  addTearDown(t.view.reset);
}

void main() {
  testWidgets('AI view counts down from 30 seconds and closes by itself', (t) async {
    tall(t);
    final repo = Spy();
    await t.pumpWidget(host(repo));
    await t.tap(find.text('✦ AI view'));
    await t.pump();
    await t.tap(find.text('Start AI view'));
    await t.pump();
    await t.pump();
    expect(find.text('Closes in 0:30'), findsOneWidget);
    await t.pump(const Duration(seconds: 10));
    expect(find.text('Closes in 0:20'), findsOneWidget);
    await t.pump(const Duration(seconds: 20));
    expect(find.text('Start again'), findsOneWidget);
    expect(find.textContaining('closes after 30 seconds'), findsOneWidget);
  });

  testWidgets('Keep watching adds time until the limit, then says so', (t) async {
    tall(t);
    final repo = Spy();
    await t.pumpWidget(host(repo));
    await t.tap(find.text('✦ AI view'));
    await t.pump();
    await t.tap(find.text('Start AI view'));
    await t.pump();
    await t.pump();
    for (var i = 0; i < 3; i++) {
      await t.tap(find.textContaining('Keep watching'));
      await t.pump();
    }
    expect(find.text('Closes in 2:00'), findsOneWidget);
    expect(find.text('Time limit reached'), findsOneWidget);
    expect(repo.calls.where((c) => c == 'extend').length, 3);
    await t.pump(const Duration(seconds: 130)); // run it out so no timer is left
  });

  testWidgets('Live view lasts 2 minutes; Stop ends it and tells the server', (t) async {
    tall(t);
    final repo = Spy();
    await t.pumpWidget(host(repo));
    await t.tap(find.text('Start live view'));
    await t.pump();
    await t.pump();
    expect(find.text('Closes in 2:00'), findsOneWidget);
    await t.tap(find.text('Stop'));
    await t.pump();
    expect(repo.calls, ['start live', 'stop']);
    expect(find.text('Start live view'), findsOneWidget);
  });

  testWidgets('leaving the page ends the view', (t) async {
    tall(t);
    final repo = Spy();
    await t.pumpWidget(host(repo));
    await t.tap(find.text('Start live view'));
    await t.pump();
    await t.pump();
    await t.pumpWidget(const MaterialApp(home: SizedBox()));
    expect(repo.calls.last, 'stop');
  });

  testWidgets('an offline camera cannot be watched', (t) async {
    tall(t);
    await t.pumpWidget(host(Spy(), working: false));
    expect(find.text('Start live view'), findsNothing);
    expect(find.textContaining('offline'), findsWidgets);
  });

  testWidgets('Fit, Fill and Enlarge change the size of the picture', (t) async {
    t.view.physicalSize = const Size(390 * 2, 844 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    await t.pumpWidget(host(Spy()));
    final fit = t.getSize(find.byType(AspectRatio).first).height;
    await t.tap(find.text('Fill'));
    await t.pump();
    final fill = t.getSize(find.byType(ClipRRect).first).height;
    await t.tap(find.text('Enlarge'));
    await t.pump();
    final enlarge = t.getSize(find.byType(ClipRRect).first).height;
    expect(fill, greaterThan(fit * 2), reason: 'Fill is about 60% of the screen');
    expect(enlarge, greaterThan(fill), reason: 'Enlarge uses the phone height, about 72%');
    expect(enlarge / 844, greaterThan(0.6));
  });
}
