import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ivision_app/api_client.dart';
import 'package:ivision_app/geometry/polygon.dart';
import 'package:ivision_app/main.dart';
import 'package:ivision_app/repo.dart';
import 'package:ivision_app/widgets/area_editor.dart';

Future<void> phone(WidgetTester t) async {
  t.view.physicalSize = const Size(390 * 2, 844 * 2);
  t.view.devicePixelRatio = 2;
  addTearDown(t.view.reset);
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('Home says everything is working, then names the problems', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(find.text('Everything is working'), findsOneWidget);
    expect(find.text('Needs you'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
    await t.pumpWidget(IVisionApp(key: UniqueKey(), repo: MockRepository(problems: true)));
    await t.pumpAndSettle();
    expect(find.textContaining('things need your attention'), findsOneWidget);
    expect(find.text('Review'), findsOneWidget);
  });

  testWidgets('Done removes an alert from Needs you', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    expect(find.text('9 people waiting for 6 minutes'), findsOneWidget);
    await t.tap(find.text('Done').first);
    await t.pumpAndSettle();
    expect(find.text('9 people waiting for 6 minutes'), findsNothing);
  });

  testWidgets('the area editor: tap corners, close, reshape, undo, redraw', (t) async {
    final c = AreaController();
    await t.pumpWidget(MaterialApp(home: Scaffold(body: SingleChildScrollView(child: Padding(padding: const EdgeInsets.all(16), child: AreaEditor(controller: c))))));
    final box = t.getRect(find.byType(AspectRatio).first);
    for (final p in const [Offset(.2, .2), Offset(.7, .2), Offset(.7, .7), Offset(.2, .7)]) {
      await t.tapAt(box.topLeft + Offset(p.dx * box.width, p.dy * box.height));
      await t.pump();
    }
    expect(c.points.length, 4);
    expect(c.closed, isFalse);
    expect(c.canClose, isTrue);
    await t.tap(find.text('Close the shape'));
    await t.pump();
    expect(c.valid, isTrue);
    expect(find.textContaining('Area set'), findsOneWidget);

    // drag the third corner outwards
    final before = c.points[2];
    await t.dragFrom(box.topLeft + Offset(before.x * box.width, before.y * box.height), const Offset(80, 30));
    await t.pump();
    expect(c.points[2].x, greaterThan(before.x));
    expect(checkShape(c.points), isNull);

    await t.tap(find.text('Undo'));
    await t.pump();
    expect(c.closed, isFalse);
    expect(c.points.length, 3);
    await t.tap(find.text('Redraw'));
    await t.pump();
    expect(c.isEmpty, isTrue);
  });

  test('a crossing shape cannot be closed or saved', () {
    final c = AreaController();
    for (final p in const [Pt(.1, .1), Pt(.6, .6), Pt(.6, .1), Pt(.1, .6)]) {
      c.add(p);
    }
    expect(c.canClose, isFalse);
    expect(c.close(), isFalse);
    expect(c.valid, isFalse);
  });

  testWidgets('the new-alert flow asks for a camera and an area before it can continue', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('Alerts').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('Smart Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Long queue'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Which camera?'), findsOneWidget);
    expect(tester(t, 'Continue').onPressed, isNull, reason: 'a camera must be chosen');
    await t.scrollUntilVisible(find.text('Billing queue'), 200);
    await t.tap(find.text('Billing queue'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Draw the area on the picture'), findsOneWidget);
    expect(tester(t, 'Continue').onPressed, isNull, reason: 'cannot continue until an area is drawn');
  });

  testWidgets('a system alert skips the camera step and saves through the repository', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('Alerts').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('System Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Connection keeps dropping'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Set it up'), findsOneWidget, reason: 'no camera step for a system alert');
    expect(find.textContaining('applies to all your cameras'), findsOneWidget);
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Who hears about it?'), findsOneWidget);
    await t.ensureVisible(find.text('Save alert'));
    await t.pump();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    expect(find.text('Your alert is on'), findsOneWidget);
  });

  testWidgets('the sign-in screen asks for email and password and shows the server\'s message', (t) async {
    final client = ApiClient(base: 'http://x', prefs: await SharedPreferences.getInstance(), client: MockClient((r) async => http.Response('{"error":{"code":"invalid_credentials","message":"That email or password is not right."}}', 401)));
    await t.pumpWidget(IVisionApp(client: client));
    await t.pump();
    await t.pump(const Duration(milliseconds: 200));
    expect(find.text('Welcome back'), findsOneWidget);
    await t.enterText(find.byType(TextField).first, 'a@b.test');
    await t.enterText(find.byType(TextField).last, 'wrong');
    await t.tap(find.text('Sign in').last);
    await t.pumpAndSettle();
    expect(find.text('That email or password is not right.'), findsOneWidget);
  });
}

FilledButton tester(WidgetTester t, String label) => t.widget<FilledButton>(find.ancestor(of: find.text(label), matching: find.byType(FilledButton)).first);
