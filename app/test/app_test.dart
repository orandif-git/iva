import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ivision_app/screens/common.dart' show Segments;
import 'package:ivision_app/api_client.dart';
import 'package:ivision_app/browser.dart';
import 'package:ivision_app/geometry/polygon.dart';
import 'package:ivision_app/main.dart';
import 'package:ivision_app/save_file.dart';
import 'package:ivision_app/screens/more.dart';
import 'package:ivision_app/screens/recorders.dart';
import 'package:ivision_app/theme.dart';
import 'package:ivision_app/models.dart';
import 'package:ivision_app/push.dart';
import 'package:ivision_app/repo.dart';
import 'package:ivision_app/widgets/alert_evidence.dart';
import 'package:ivision_app/screens/activity.dart';
import 'package:ivision_app/widgets/camera_picture.dart';
import 'package:ivision_app/widgets/count_line_editor.dart';
import 'package:ivision_app/screens/camera_setup.dart';
import 'package:ivision_app/screens/wizard.dart';
import 'package:ivision_app/widgets/glass_nav.dart';
import 'package:ivision_app/widgets/profile_menu.dart';
import 'package:ivision_app/widgets/area_editor.dart';

Future<void> phone(WidgetTester t) async {
  t.view.physicalSize = const Size(390 * 2, 844 * 2);
  t.view.devicePixelRatio = 2;
  addTearDown(t.view.reset);
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  profileTests();
  testWidgets('Home says everything is working, then names the problems', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(find.text('Everything is working'), findsOneWidget);
    expect(find.text('Needs you'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
    await t.pumpWidget(IVisionApp(key: UniqueKey(), repo: MockRepository(problems: true)));
    await t.pumpAndSettle();
    expect(find.textContaining('things need attention'), findsOneWidget);
    expect(find.text('Needs attention'), findsOneWidget);
    expect(find.textContaining('Stockroom camera'), findsWidgets);
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
    await t.tap(find.text('AI Watch').last);
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
    await t.scrollUntilVisible(find.text('Billing queue'), 200, scrollable: find.byType(Scrollable).first);
    await t.pumpAndSettle();
    await t.tap(find.text('Billing queue'), warnIfMissed: false);
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Draw the area on the picture'), findsOneWidget);
    expect(tester(t, 'Continue').onPressed, isNull, reason: 'cannot continue until an area is drawn');
  });

  testWidgets('a queue alert asks how often to take an evidence picture (a number, 15 minutes unless changed), and saves the choice', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('Smart Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Long queue'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Cash counter'), 200, scrollable: find.byType(Scrollable).first);
    await t.tap(find.text('Cash counter'), warnIfMissed: false);
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    final box = t.getRect(find.byType(AspectRatio).first);
    for (final p in const [Offset(.2, .2), Offset(.7, .2), Offset(.7, .7), Offset(.2, .7)]) {
      await t.tapAt(box.topLeft + Offset(p.dx * box.width, p.dy * box.height));
      await t.pump();
    }
    await t.tap(find.text('Close the shape'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Take another picture every'), 200, scrollable: find.byType(Scrollable).first);
    expect(find.text('15'), findsOneWidget, reason: 'fifteen minutes unless the customer changes it, entered like the other numbers');
    expect(find.textContaining('another every 15 minutes'), findsOneWidget);
    await t.tap(find.byTooltip('Less').last);
    await t.pumpAndSettle();
    expect(find.text('14'), findsOneWidget);
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.ensureVisible(find.text('Save alert'));
    await t.pump();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    expect(repo.areaList.last.cameraId, 'c2');
    expect(repo.areaList.last.params['picture_minutes'], 14, reason: 'the choice is saved on the area');
  });

  testWidgets('the plain "Too many people" alert has the same picture setting', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('Smart Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Occupancy limit exceeded'));
    await t.pump();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Take another picture every'), findsOneWidget);
    expect(find.text('15'), findsOneWidget);
    await t.tap(find.byTooltip('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.ensureVisible(find.text('Save alert'));
    await t.pump();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    final saved = (await repo.alertDefs()).firstWhere((d) => d.type == 'crowding');
    expect(saved.params['picture_minutes'], 16);
  });

  testWidgets('AI Watch lists the areas an alert is watching, and tapping one loads its shape and numbers to change in place', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.textContaining('Smart alerts ('));
    await t.pumpAndSettle();
    expect(find.text('Billing queue'), findsNothing, reason: 'the areas are folded away until you open the alert');
    expect(find.text('1 area'), findsOneWidget, reason: 'how many areas the alert watches, beside its name');
    await t.tap(find.text('Long queue'));
    await t.pumpAndSettle();
    expect(find.text('Billing queue'), findsOneWidget, reason: 'the camera the queue alert is watching');
    expect(find.textContaining('When more than 6 people waiting'), findsOneWidget);
    expect(find.text('Add another area'), findsWidgets);
    await t.tap(find.text('Billing queue'));
    await t.pumpAndSettle();
    expect(find.text('Set it up'), findsOneWidget, reason: 'straight to the settings: the camera and the area are already known');
    expect(find.textContaining('Area set'), findsOneWidget, reason: 'the saved shape is loaded, not "Not drawn yet"');
    expect(find.text('Not drawn yet'), findsNothing);
    expect(find.text('6'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Take another picture every'), 200, scrollable: find.byType(Scrollable).first);
    await t.tap(find.byTooltip('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Who hears about it?'), findsOneWidget);
    await t.tap(find.textContaining('Ravi').first);          // this alert had nobody to tell yet
    await t.pumpAndSettle();
    await t.ensureVisible(find.text('Save alert'));
    await t.pump();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    expect(repo.areaList.length, 2, reason: 'changed in place, not added a second time');
    expect(repo.areaList.firstWhere((a) => a.id == 'z1').params['picture_minutes'], 16);
    expect(repo.areaList.firstWhere((a) => a.id == 'z1').params['length'], 6);
  });

  testWidgets('a system alert skips the camera step and saves through the repository', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('System Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Unstable connection'));
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

  testWidgets('quiet hours are set where you choose who hears about it, in plain words, and are saved with the alert', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.text('New alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('System Alerts'));
    await t.pumpAndSettle();
    await t.tap(find.text('Unstable connection'));
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    await t.tap(find.text('Continue'));
    await t.pumpAndSettle();
    expect(find.text('Who hears about it?'), findsOneWidget);
    expect(find.text('Do not message me at night'), findsOneWidget);
    expect(find.text('Off: messages come at any time'), findsOneWidget);
    await t.tap(find.byType(Switch));
    await t.pumpAndSettle();
    expect(find.text('No messages from 10 pm to 7 am'), findsOneWidget, reason: 'ten at night to seven in the morning unless changed');
    expect(find.textContaining('Nothing is lost'), findsOneWidget, reason: 'says plainly that the alert is still saved in the app');
    await t.tap(find.byTooltip('Stop messages at: one hour later'));
    await t.pumpAndSettle();
    expect(find.text('No messages from 11 pm to 7 am'), findsOneWidget);
    await t.ensureVisible(find.text('Save alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    expect(repo.saved.last.quietStart, '23:00');
    expect(repo.saved.last.quietEnd, '07:00');
  });

  testWidgets('an urgent alert has no quiet hours: it is always sent', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Recorder offline'));
    await t.pumpAndSettle();
    expect(find.text('Who hears about it?'), findsOneWidget);
    expect(find.textContaining('always sent, day and night'), findsOneWidget);
    expect(find.text('Do not message me at night'), findsNothing);
    await t.tap(find.textContaining('Ravi').first);
    await t.pumpAndSettle();
    await t.ensureVisible(find.text('Save alert'));
    await t.pumpAndSettle();
    await t.tap(find.text('Save alert'));
    await t.pumpAndSettle();
    expect(repo.saved.last.quietStart, isNull);
  });

  testWidgets('Home shows a payment that is due with its date, and nothing when there is none', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    expect(find.textContaining('Payment'), findsNothing);
    repo.payment = const PaymentDue('INV-2026-0042', '₹8,994', '15 Nov 2026', false);
    await t.tap(find.text('Cameras').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Home').last);
    await t.pumpAndSettle();
    expect(find.text('Payment due on 15 Nov 2026'), findsOneWidget);
    expect(find.text('₹8,994 · Invoice INV-2026-0042'), findsOneWidget);
    repo.payment = const PaymentDue('INV-2026-0042', '₹8,994', '15 Nov 2026', true);
    await t.tap(find.text('Cameras').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Home').last);
    await t.pumpAndSettle();
    expect(find.text('Payment was due on 15 Nov 2026'), findsOneWidget);
  });

  testWidgets('Settings has opening hours, off until set, with plain words about what needs them', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2800 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    expect(find.text('Opening hours'.toUpperCase()), findsOneWidget);
    expect(find.text('Not set'), findsOneWidget);
    expect(find.textContaining('Without your hours, neither can work'), findsOneWidget);
    await t.tap(find.byWidgetPredicate((w) => w is Switch && !w.value).first);
    await t.pumpAndSettle();
    expect(repo.hours.open, 9);
    expect(repo.hours.close, 21);
    expect(find.text('Open 9 am to 9 pm'), findsOneWidget);
    await t.tap(find.byTooltip('Closes at: one hour later'));
    await t.pumpAndSettle();
    expect(repo.hours.close, 22);
    await t.tap(find.text('Sun'));
    await t.pumpAndSettle();
    expect(repo.hours.closedDays, [6]);
  });

  testWidgets('a recorder page says in plain words how its disks, storage, kept video and clock are', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2400 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    final r = (await repo.recorders()).first;
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: RecorderPage(repo: repo, recorder: r, canEdit: true)));
    await t.pumpAndSettle();
    expect(find.text('How the recorder is doing'.toUpperCase()), findsOneWidget);
    expect(find.text('The disk is healthy'), findsOneWidget);
    expect(find.text('65% full'), findsOneWidget);
    expect(find.text('About 21 days of video kept'), findsOneWidget);
    expect(find.text('The clock is right'), findsOneWidget);
    expect(find.text('Running for 21 days'), findsOneWidget);
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: Scaffold(body: RecorderHealthCard(RecorderHealth(disks: const [DiskInfo(0, 4000000, 200000, 2, null)], percentFull: 95, clockOutMinutes: 12)))));
    await t.pumpAndSettle();
    expect(find.text('A disk has a fault'), findsOneWidget);
    expect(find.text('95% full'), findsOneWidget);
    expect(find.text('The clock is 12 minutes out'), findsOneWidget);
  });

  testWidgets('the sign-in screen asks for email and password and shows the server\'s message', (t) async {
    final client = ApiClient(base: 'http://x', prefs: await SharedPreferences.getInstance(), client: MockClient((r) async => http.Response('{"error":{"code":"invalid_credentials","message":"That email or password is not right."}}', 401)));
    await t.pumpWidget(IVisionApp(client: client));
    await t.pump();
    await t.pump(const Duration(milliseconds: 200));
    expect(find.text('Welcome back'), findsOneWidget);
    await t.enterText(find.byType(TextField).first, 'a@b.test');
    await t.enterText(find.byType(TextField).last, 'wrong');
    await t.ensureVisible(find.text('Sign in').last);
    await t.pump();
    await t.tap(find.text('Sign in').last);
    await t.pumpAndSettle();
    expect(find.text('That email or password is not right.'), findsOneWidget);
  });
}

FilledButton tester(WidgetTester t, String label) => t.widget<FilledButton>(find.ancestor(of: find.text(label), matching: find.byType(FilledButton)).first);

void profileTests() {
  testWidgets('the top bar has the logo and a profile button, and no duplicate title', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(find.bySemanticsLabel(RegExp('Jas iVision')), findsWidgets);
    expect(find.byType(ProfileButton), findsOneWidget);
    expect(find.text('Home'), findsOneWidget, reason: 'only the tab says Home');
    expect(find.byIcon(Icons.dark_mode_outlined), findsNothing, reason: 'the light/dark switch lives in the profile menu');
  });

  testWidgets('the profile menu has Light, Dark, Auto, Change password and Sign out', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.byType(ProfileButton));
    await t.pumpAndSettle();
    for (final x in ['Priya', 'Green Valley Store', 'APPEARANCE', 'Light', 'Dark', 'Auto', 'Change password', 'Sign out']) {
      expect(find.text(x), findsWidgets, reason: x);
    }
    await t.tap(find.text('Dark'));
    await t.pumpAndSettle();
    expect(Theme.of(t.element(find.text('Appearance').evaluate().isEmpty ? find.text('Sign out') : find.text('Appearance'))).brightness, Brightness.dark);
    expect((await SharedPreferences.getInstance()).getString('gi_next_theme'), 'dark');
  });

  testWidgets('Change password opens inside the menu, with no pop-up, and passes the sign-out choice', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.byType(ProfileButton));
    await t.pumpAndSettle();
    await t.tap(find.text('Change password'));
    await t.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
    for (final x in ['Current password', 'New password', 'Confirm new password', 'Sign out of all other devices']) {
      expect(find.text(x), findsOneWidget, reason: x);
    }
    final fields = find.byType(TextField);
    await t.enterText(fields.at(0), 'old password 1');
    await t.enterText(fields.at(1), 'a long new password');
    await t.enterText(fields.at(2), 'something else entirely');
    await t.ensureVisible(find.widgetWithText(FilledButton, 'Change password'));
    await t.pumpAndSettle();
    await t.tap(find.widgetWithText(FilledButton, 'Change password'));
    await t.pumpAndSettle();
    expect(find.text('The two new passwords are not the same.'), findsOneWidget);
    await t.enterText(fields.at(2), 'a long new password');
    await t.tap(find.text('Sign out of all other devices'));
    await t.pumpAndSettle();
    await t.ensureVisible(find.widgetWithText(FilledButton, 'Change password'));
    await t.pumpAndSettle();
    await t.tap(find.widgetWithText(FilledButton, 'Change password'));
    await t.pumpAndSettle();
    expect(repo.lastSignOutOthers, false);
    expect(find.text('Password changed.'), findsOneWidget);
    expect(find.text('Sign out'), findsWidgets, reason: 'this device stays signed in');
  });

  testWidgets('the theme starts on light, whatever the device says, and remembers a choice', (t) async {
    SharedPreferences.setMockInitialValues({'gi_next_theme': 'auto'});
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(t.widget<MaterialApp>(find.byType(MaterialApp)).themeMode, ThemeMode.system);
    SharedPreferences.setMockInitialValues({});
    await t.pumpWidget(const SizedBox());
    await t.pumpWidget(IVisionApp(key: UniqueKey(), repo: MockRepository()));
    await t.pumpAndSettle();
    expect(t.widget<MaterialApp>(find.byType(MaterialApp)).themeMode, ThemeMode.light);
  });

  testWidgets('the profile menu drops down from the profile button, not up from the bottom', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    final button = t.getRect(find.byType(ProfileButton));
    await t.tap(find.byType(ProfileButton));
    await t.pumpAndSettle();
    expect(find.byType(BottomSheet), findsNothing);
    final menu = t.getRect(find.text('APPEARANCE'));
    expect(menu.top, greaterThan(button.bottom), reason: 'below the button');
    expect(menu.top, lessThan(400), reason: 'near the top, not at the bottom');
    await t.tapAt(const Offset(40, 800)); // tap outside: it closes
    await t.pumpAndSettle();
    expect(find.text('APPEARANCE'), findsNothing);
  });

  testWidgets('Alerts is only the inbox (the bell); what you watch for is in AI Watch, split into System and Smart', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(find.descendant(of: find.byType(AppTabBar), matching: find.text('Alerts')), findsNothing, reason: 'Alerts is not in the bar any more');
    await t.tap(find.bySemanticsLabel(RegExp('^Alerts')));
    await t.pumpAndSettle();
    expect(find.text('My alerts'), findsNothing, reason: 'no tabs: viewing and setting up are not mixed');
    expect(find.text('Needs you'), findsOneWidget);
    expect(find.text('History'), findsOneWidget);
    expect(find.textContaining('Watching for'), findsNothing, reason: 'no duplicate Manage card: AI Watch is its own tab');
    expect(find.text('Manage'), findsNothing);
    expect(find.text('New alert'), findsNothing, reason: 'the inbox has no set-up button');
    await t.pageBack();
    await t.pumpAndSettle();
    await t.tap(find.text('AI Watch').last);
    await t.pumpAndSettle();
    expect(find.textContaining('System alerts ('), findsOneWidget);
    expect(find.textContaining('Smart alerts ('), findsOneWidget);
    expect(find.text('New alert'), findsOneWidget);
    expect(find.text('ON'), findsOneWidget);
    expect(find.text('Camera offline'), findsOneWidget, reason: 'a system alert');
    expect(find.text('Long queue'), findsNothing, reason: 'smart alerts are on their own tab');
    await t.tap(find.textContaining('Smart alerts ('));
    await t.pumpAndSettle();
    expect(find.text('Long queue'), findsOneWidget);
    expect(find.text('Camera offline'), findsNothing);
    await t.tap(find.byType(Switch).first);
    await t.pumpAndSettle();
  });

  testWidgets('each place keeps where you were, and switching is instant', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    expect(find.text('Needs you'), findsOneWidget);
    await t.tap(find.text('Cameras').last);
    await t.pump(); // one frame: no fade to wait for
    await t.pumpAndSettle();
    await t.tap(find.text('Home').last);
    await t.pump();
    expect(find.text('Needs you'), findsOneWidget, reason: 'Home is still there, no reload spinner');
  });

  testWidgets('five tabs: Home, Cameras, AI Watch, Activity, More, and they fit without overlapping', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    final bar = find.byType(AppTabBar);
    var last = -1.0;
    for (final x in ['Home', 'Cameras', 'AI Watch', 'Activity', 'More']) {
      final r = t.getRect(find.descendant(of: bar, matching: find.text(x)));
      expect(r.left, greaterThanOrEqualTo(last - 0.5), reason: '$x must not overlap the one before');
      expect(r.height, lessThan(16), reason: '$x stays on one line');
      last = r.right;
    }
    expect(find.descendant(of: bar, matching: find.text('Reports')), findsNothing);
  });

  testWidgets('the tab bar floats above the page as a rounded pill', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    final bar = t.getRect(find.byKey(const ValueKey('tab-bar-pill')));
    expect(bar.left, greaterThan(8), reason: 'not edge to edge');
    expect(bar.right, lessThan(390 - 8));
    expect(bar.bottom, lessThan(844 - 4), reason: 'it hovers, it does not sit on the edge of the screen');
    final box = t.widget<Container>(find.byKey(const ValueKey('tab-bar-pill'))).decoration as BoxDecoration;
    expect(box.borderRadius, BorderRadius.circular(34), reason: 'a pill');
    expect(box.boxShadow, isNotEmpty, reason: 'a soft shadow lifts it off the page');
  });

  testWidgets('Cameras are grouped by recorder and tell you if they are all working', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('Cameras').last);
    await t.pumpAndSettle();
    expect(find.text('All 8 cameras are working'), findsOneWidget);
    // one grid of every camera, with a pill for each recorder to narrow it
    expect(find.text('All 8'), findsOneWidget);
    expect(find.text('Front door'), findsOneWidget);
    await t.ensureVisible(find.text('Godown recorder').first);      // the pills scroll sideways on a narrow phone
    await t.pumpAndSettle();
    await t.tap(find.text('Godown recorder').first);
    await t.pumpAndSettle();
    expect(find.text('Front door'), findsNothing, reason: 'the Store recorder\'s cameras are filtered out');
    await t.ensureVisible(find.text('All 8'));
    await t.pumpAndSettle();
    await t.tap(find.text('All 8'));
    await t.pumpAndSettle();
    expect(find.text('Front door'), findsOneWidget);
    expect(find.textContaining('Needs attention'), findsNothing, reason: 'nothing to filter when all work');
  });

  testWidgets('with a camera down, Cameras says so and can show only what needs attention', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository(problems: true)));
    await t.pumpAndSettle();
    await t.tap(find.text('Cameras').last);
    await t.pumpAndSettle();
    expect(find.text('1 of 8 cameras need attention'), findsOneWidget);
    await t.tap(find.textContaining('Needs attention ('));
    await t.pumpAndSettle();
    expect(find.text('Stockroom'), findsOneWidget);
    expect(find.text('Front door'), findsNothing);
  });

  testWidgets('Settings shows your plan, people, recorders and account, and can add a person', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    expect(find.text('Full'), findsOneWidget);
    expect(find.text('8 of 20 cameras'), findsOneWidget);
    expect(find.text('PEOPLE WHO GET ALERTS'), findsOneWidget);
    expect(find.text('Ravi, store manager'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Store recorder'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.text('Store recorder'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Sign out'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.text('Change password'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Add'), -600, scrollable: find.byType(Scrollable).first);
    await t.tap(find.text('Add'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField).at(0), 'Anita');
    await t.enterText(find.byType(TextField).at(1), '98765 43299');
    await t.tap(find.text('Add').last);
    await t.pumpAndSettle();
    expect(find.textContaining('agreed'), findsWidgets, reason: 'consent is required first');
    await t.tap(find.byType(Checkbox));
    await t.pump();
    await t.tap(find.text('Add').last);
    await t.pumpAndSettle();
    expect((await repo.people()).map((p) => p.name), contains('Anita'));
  });

  test('only people with a mobile number can be sent a report', () {
    expect(const Person('1', 'A', '+91 98•••• 3210').hasPhone, isTrue);
    expect(const Person('2', 'B', '', email: 'b@x.test').hasPhone, isFalse);
  });

  test('the alert is described in one plain sentence from its numbers', () {
    final q = AlertDef(type: 'queue_long', title: 'Long queue', description: '', smart: true, area: true);
    expect(describe(q, {'length': 8, 'minutes': 4}, where: 'Billing queue'), 'Alert me when more than 8 people are waiting in the area on Billing queue for 4 minutes or more.');
    final c = AlertDef(type: 'connection_unstable', title: 'x', description: '', smart: false);
    expect(describe(c, {'drops': 3}), contains('3 times in an hour'));
    final ph = AlertDef(type: 'phone_use', title: 'x', description: '', smart: true, area: true);
    expect(describe(ph, {'seconds': 40}), allOf(contains('40 seconds'), contains('never names a person')));
  });

  test('a report schedule reads like a sentence', () {
    expect(ReportSchedule(type: 'footfall_summary', frequency: 'daily', sendAt: '21:00').when, 'Every day at 9 pm');
    expect(ReportSchedule(type: 'footfall_summary', frequency: 'weekly', weekday: 0, sendAt: '08:00').when, 'Mondays at 8 am');
    expect(ReportSchedule(type: 'vehicle_audit', frequency: 'monthly', dayOfMonth: 1, sendAt: '07:00').when, 'On the 1st at 7 am');
    expect(ReportSchedule(type: 'vehicle_audit', frequency: 'monthly', dayOfMonth: 22, sendAt: '12:00').when, 'On the 22nd at 12 pm');
    expect(ReportSchedule(type: 'gate_register', frequency: 'weekly', weekday: 4, sendAt: '07:00', recipientIds: ['a']).toJson(), {'report_type': 'gate_register', 'frequency': 'weekly', 'send_at': '07:00', 'weekday': 4, 'recipient_ids': ['a'], 'enabled': true});
  });

  testWidgets('a ready report can be downloaded as a spreadsheet', (t) async {
    await phone(t);
    final saved = <String>[];
    fileSaver = (name, bytes, mime) async { saved.add('$name ${bytes.length} $mime'); };
    addTearDown(() => fileSaver = saveFile);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Reports'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Recent reports'.toUpperCase()), 300, scrollable: find.byType(Scrollable).first);
    await t.drag(find.byType(Scrollable).first, const Offset(0, -200));
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Download Footfall summary').first);
    await t.pumpAndSettle();
    expect(saved.length, 1);
    expect(saved.first, contains('spreadsheetml.sheet'));
    expect(find.textContaining('Downloaded'), findsOneWidget);
  });

  testWidgets('adding a recorder: values to type in, checks tick on their own, then pick cameras', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: RecorderSetupPage(repo: repo, pollEvery: const Duration(milliseconds: 200))));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'Godown');
    await t.tap(find.text('Continue'));
    await t.pump();
    await t.pump(const Duration(milliseconds: 50));
    for (final x in ['connect.example.in', '7681', 'GI-AB12CD', 'giview', 'k3y-once-only']) {
      expect(find.text(x), findsOneWidget, reason: x);
    }
    expect(find.textContaining('shown only now'), findsOneWidget);
    expect(find.text('Choose your cameras'.toUpperCase()), findsNothing, reason: 'nothing found yet');
    await t.pump(const Duration(milliseconds: 500));
    await t.pumpAndSettle(const Duration(milliseconds: 100));
    await t.scrollUntilVisible(find.text('3. Choose your cameras'.toUpperCase()), 200, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.widgetWithText(FilledButton, 'Add').first);
    await t.pumpAndSettle();
    await t.tap(find.text('Add').first);
    await t.pumpAndSettle();
    expect(find.text('Added'), findsWidgets);
  });

  testWidgets('a recorder page shows its details and lets the owner rename and remove it', (t) async {
    await phone(t);
    final repo = MockRepository();
    final r = (await repo.recorders()).first;
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: RecorderPage(repo: repo, recorder: r, canEdit: true)));
    await t.pumpAndSettle();
    expect(find.text('Connected'), findsOneWidget);
    await t.scrollUntilVisible(find.text('connect.example.in'), 300, scrollable: find.byType(Scrollable).first);       // below the recorder's health
    expect(find.text('connect.example.in'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Back door'), -300, scrollable: find.byType(Scrollable).first);
    expect(find.text('Back door'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Rename'), 300, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.text('Rename'));
    await t.pumpAndSettle();
    await t.tap(find.text('Rename'));
    await t.pumpAndSettle();
    await t.enterText(find.widgetWithText(TextField, 'Name'), 'Main shop');
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect(repo.lastRenamed, 'Main shop');
    await t.scrollUntilVisible(find.text('Make a new viewer password'), 300, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.text('Make a new viewer password'));
    await t.pumpAndSettle();
    await t.tap(find.text('Make a new viewer password'));
    await t.pumpAndSettle();
    await t.tap(find.text('Make new password'));
    await t.pumpAndSettle();
    expect(find.text('brand-new-key'), findsOneWidget);
    await t.scrollUntilVisible(find.text('Remove this recorder'), 300, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.text('Remove this recorder'));
    await t.pumpAndSettle();
    await t.tap(find.text('Remove this recorder'));
    await t.pumpAndSettle();
    expect(find.textContaining('cannot be undone'), findsOneWidget);
    await t.tap(find.text('Remove it'));
    await t.pumpAndSettle();
    expect(repo.removed, true);
  });

  testWidgets('Settings lets the owner add a recorder, and opens one when tapped', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Add a recorder'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.text('Store recorder'), findsOneWidget);
    await t.tap(find.text('Store recorder'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('What is typed into the recorder'.toUpperCase()), 300, scrollable: find.descendant(of: find.byType(RecorderPage), matching: find.byType(Scrollable)).first);
    expect(find.text('What is typed into the recorder'.toUpperCase()), findsOneWidget);
  });

  testWidgets('push: a device that already allowed notifications is registered quietly after sign-in, and forgotten on sign-out', (t) async {
    await phone(t);
    final repo = MockRepository()..push = true;
    await t.pumpWidget(IVisionApp(repo: repo, push: _FakePush('tok-123456789012345678', 'android', granted: true)));
    await t.pumpAndSettle();
    expect(repo.registered, ['tok-123456789012345678 android']);
    await t.tap(find.byType(ProfileButton));
    await t.pumpAndSettle();
    await t.tap(find.text('Sign out'));
    await t.pumpAndSettle();
    expect(repo.registered, isEmpty, reason: 'forgotten on sign-out');
  });

  testWidgets('push: nothing opens or registers by itself; the Turn on button in Settings does it', (t) async {
    await phone(t);
    final repo = MockRepository()..push = true;
    final fake = _FakePush('tok-123456789012345678', 'web');
    await t.pumpWidget(IVisionApp(repo: repo, push: fake));
    await t.pumpAndSettle();
    expect(repo.registered, isEmpty, reason: 'no permission box without a tap');
    expect(fake.asked, 0);
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Push notifications'), 300, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.text('Turn on'));
    await t.pumpAndSettle();
    await t.tap(find.text('Turn on'));
    await t.pumpAndSettle();
    expect(fake.asked, 1);
    expect(repo.registered, ['tok-123456789012345678 web']);
    expect(find.text('Notifications are on for this device.'), findsOneWidget);
    await t.tap(find.text('Turn off'));
    await t.pumpAndSettle();
    expect(repo.registered, isEmpty);
  });

  testWidgets('push: when the person says no, or the server has push off, the button says so and nothing is registered', (t) async {
    await phone(t);
    final repo = MockRepository()..push = true;
    await t.pumpWidget(IVisionApp(repo: repo, push: _FakePush(null, 'web')));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Push notifications'), 300, scrollable: find.byType(Scrollable).first);
    await t.ensureVisible(find.text('Turn on'));
    await t.pumpAndSettle();
    await t.tap(find.text('Turn on'));
    await t.pumpAndSettle();
    expect(find.textContaining('blocked on this device'), findsOneWidget);
    expect(repo.registered, isEmpty);
  });

  testWidgets('push: an iPhone in Safari is told to add the app to the Home Screen', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()..push = true, push: _FakePush(null, 'web', supported: false, hint: 'ios-install')));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Push notifications'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('Add to Home Screen'), findsOneWidget);
    expect(find.text('Turn on'), findsNothing);
  });

  testWidgets('push: a device that cannot receive it keeps the plain note', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()..push = true));          // the default: no push on this device
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Settings'));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(find.text('Push notifications'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.text('Turn on'), findsNothing);
    expect(find.textContaining('Not available in this browser'), findsOneWidget);
  });

  testWidgets('a staff member signing in on the customer page is handed to the staff console', (t) async {
    await phone(t);
    SharedPreferences.setMockInitialValues({});
    final fake = _FakeBrowser();
    final client = ApiClient(base: 'https://x.test', client: MockClient((r) async => http.Response(jsonEncode({'kind': 'staff', 'access_token': 'sa', 'refresh_token': 'staff-rt'}), 200)));
    await t.pumpWidget(IVisionApp(client: client, browser: fake));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField).at(0), 'admin@x.test');
    await t.enterText(find.byType(TextField).at(1), 'a long password');
    await t.tap(find.text('Sign in').last);
    await t.pumpAndSettle();
    expect(fake.handedOff, 'staff-rt');
    expect(find.textContaining('staff account'), findsNothing, reason: 'no more "sign in at /staff/" message');
  });

  testWidgets('an invitation link asks for a password, saves it, and goes to sign-in with a note', (t) async {
    await phone(t);
    SharedPreferences.setMockInitialValues({});
    final seen = <Map>[];
    final client = ApiClient(base: 'https://x.test', client: MockClient((r) async {
      if (r.url.path == '/v1/auth/set-password') { seen.add(jsonDecode(r.body) as Map); return http.Response('', 204); }
      return http.Response('{}', 404);
    }));
    final fake = _FakeBrowser(path: '/set-password', query: {'token': 'tok123'});
    await t.pumpWidget(IVisionApp(client: client, browser: fake));
    await t.pumpAndSettle();
    expect(find.text('Choose your password'), findsOneWidget);
    await t.enterText(find.byType(TextField).at(0), 'short');
    await t.enterText(find.byType(TextField).at(1), 'short');
    await t.tap(find.text('Save password'));
    await t.pumpAndSettle();
    expect(find.textContaining('at least 10 characters'), findsWidgets);
    await t.enterText(find.byType(TextField).at(0), 'a long enough password');
    await t.enterText(find.byType(TextField).at(1), 'something different');
    await t.tap(find.text('Save password'));
    await t.pumpAndSettle();
    expect(find.text('The two passwords are not the same.'), findsOneWidget);
    await t.enterText(find.byType(TextField).at(1), 'a long enough password');
    await t.tap(find.text('Save password'));
    await t.pumpAndSettle();
    expect(seen, [{'token': 'tok123', 'password': 'a long enough password'}]);
    expect(find.text('Your password is saved. Sign in with it now.'), findsOneWidget);
    expect(fake.url, '/');
  });

  testWidgets('an incomplete invitation link says so instead of showing a form', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(client: ApiClient(base: 'https://x.test', client: MockClient((r) async => http.Response('{}', 404))), browser: _FakeBrowser(path: '/set-password')));
    await t.pumpAndSettle();
    expect(find.textContaining('This link is not complete'), findsOneWidget);
    expect(find.text('Save password'), findsNothing);
  });

  testWidgets('staff opening a customer from the console see the customer view-only, with a banner, and Stop ends it', (t) async {
    await phone(t);
    SharedPreferences.setMockInitialValues({});
    final calls = <String>[];
    final client = ApiClient(base: 'https://x.test', client: MockClient((r) async {
      calls.add('${r.method} ${r.url.path} ${r.headers['Authorization']}');
      if (r.url.path == '/v1/auth/refresh') return http.Response('{}', 401);
      return http.Response(jsonEncode(r.url.path == '/v1/me' ? {'user': {'name': 'Priya', 'role': 'admin'}, 'tenant': {'name': 'Green Valley Store'}} : <String, dynamic>{}), 200);
    }));
    final fake = _FakeBrowser(openedByStaff: true, grant: const ViewAsGrant(token: 'view-token', sessionId: 's1', tenant: 'Green Valley Store'));
    await t.pumpWidget(IVisionApp(client: client, browser: fake));
    await t.pump();
    await t.pump(const Duration(milliseconds: 50));
    expect(find.textContaining('You are looking at', findRichText: true), findsOneWidget);
    expect(find.textContaining('Green Valley Store', findRichText: true), findsWidgets);
    expect(calls.where((c) => c.contains('/auth/refresh')), isEmpty, reason: 'a view-only token is never renewed');
    expect(calls.any((c) => c.endsWith('Bearer view-token')), true);
    await t.tap(find.text('Stop'));
    await t.pumpAndSettle();
    expect(fake.stopped, 's1');
    expect(find.textContaining('view-only session has ended'), findsOneWidget);
  });

  testWidgets('if the console never hands the session over, the tab says so', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(client: ApiClient(base: 'https://x.test', client: MockClient((r) async => http.Response('{}', 404))), browser: _FakeBrowser(openedByStaff: true)));
    await t.pumpAndSettle();
    expect(find.textContaining("Could not open the customer's app"), findsOneWidget);
  });

  testWidgets('the owner sets a camera up: counting line (swap in/out), plate area, quality, pause and remove', (t) async {
    t.view.physicalSize = const Size(390 * 2, 2600 * 2);
    t.view.devicePixelRatio = 2;
    addTearDown(t.view.reset);
    final repo = MockRepository();
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: Scaffold(body: SingleChildScrollView(child: CameraSetup(repo: repo, camera: const CameraItem('c1', 'Front door', 'Store recorder', true), onRemoved: () => repo.cameraRemoved = true)))));
    await t.pumpAndSettle();
    expect(find.text('Counting line'.toUpperCase()), findsOneWidget);
    expect(find.text('Plate area'.toUpperCase()), findsOneWidget);
    await t.tap(find.text('Swap in / out'));
    await t.pump();
    // drag the first end of the line
    await t.drag(find.bySemanticsLabel('Line end 1, drag to move'), const Offset(30, -20));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Save').first);
    await t.pumpAndSettle();
    final saved = repo.zonesSaved['c1']!;
    expect(saved.single.kind, 'count_line', reason: 'no plate area drawn yet, so only the line is saved');
    expect(saved.single.points.length, 2);
    expect(saved.single.points[0].x, isNot(0.15), reason: 'the dragged end moved');
    expect(saved.single.points[1], const Pt(0.85, 0.6));
    expect(saved.single.inDirection!.y, lessThan(0), reason: 'swapped: the arrow points up instead of down');
    expect(find.text('Saved. The camera uses it within a minute.'), findsOneWidget);
    await t.enterText(find.widgetWithText(TextField, 'Front door'), 'Main gate');
    await t.tap(find.widgetWithText(FilledButton, 'Save').last);
    await t.pumpAndSettle();
    expect(repo.renamedCameras['c1'], 'Main gate');
    expect(find.text('Name saved.'), findsOneWidget);
    await t.tap(find.text('Full quality'));
    await t.pumpAndSettle();
    expect(repo.streamSet, 'main');
    await t.tap(find.text('Pause this camera'));
    await t.pumpAndSettle();
    expect(repo.cameraEnabled, false);
    expect(find.text('Resume this camera'), findsOneWidget);
    await t.tap(find.text('Remove this camera'));
    await t.pumpAndSettle();
    await t.tap(find.text('Remove'));
    await t.pumpAndSettle();
    expect(repo.cameraRemoved, true);
  });

  test('the counting line knows which way is in, and reads it back from the server', () {
    final c = CountLineController(a: const Pt(0.2, 0.5), b: const Pt(0.8, 0.5));      // a line left to right
    expect(c.inDirection, const Pt(0, 1), reason: 'in is downwards in the picture');
    c.swap();
    expect(c.inDirection.y, -1);
    expect(CountLineController.flipFor([const Pt(0.2, 0.5), const Pt(0.8, 0.5)], const Pt(0, -1)), -1);
    expect(CountLineController.flipFor([const Pt(0.2, 0.5), const Pt(0.8, 0.5)], const Pt(0, 1)), 1);
    expect(CountLineController.flipFor([const Pt(0.2, 0.5), const Pt(0.8, 0.5)], null), 1);
  });

  testWidgets('an alert with evidence shows what the camera saw, and the video opens through a short-lived link', (t) async {
    await phone(t);
    final repo = MockRepository();
    final opened = <Uri>[];
    linkOpener = (u) async { opened.add(u); return true; };
    addTearDown(() => linkOpener = (u) async => false);
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.bySemanticsLabel(RegExp('^Alerts')));
    await t.pumpAndSettle();
    expect(find.byIcon(Icons.videocam_outlined, skipOffstage: false), findsWidgets, reason: 'the queue alert has a picture and a video');
    expect(find.byIcon(Icons.photo_camera_outlined, skipOffstage: false), findsWidgets, reason: 'the phone-use alert has a picture only');
    await t.tap(find.text('9 people waiting for 6 minutes'));
    await t.pumpAndSettle();
    expect(find.byType(Image), findsWidgets, reason: 'the picture is shown');
    expect(find.text('Open full size'), findsNothing);
    await t.ensureVisible(find.text('Watch the video'));
    await t.pumpAndSettle();
    await t.tap(find.text('Watch the video'));
    await t.pumpAndSettle();
    expect(repo.linksOpened, ['a1']);
    expect(opened.single.toString(), 'https://example.test/e/token-a1');
    await t.tap(find.text('Possible phone use at the counter'));
    await t.pumpAndSettle();
    expect(find.text('Open full size'), findsOneWidget, reason: 'no video for this one, but the full picture opens');
  });

  testWidgets('evidence that has been deleted says so plainly', (t) async {
    final repo = MockRepository();
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: Scaffold(body: AlertEvidence(repo: repo, alert: AlertItem(id: 'gone', title: 'x', body: '', rule: 'queue_long', where: 'q', when: 'now', hasEvidence: true)))));
    await t.pumpAndSettle();
    expect(find.text('The picture is no longer stored.'), findsOneWidget);
    await t.tap(find.text('Open full size'));
    await t.pumpAndSettle();
    expect(find.textContaining('may have been deleted after the retention period'), findsOneWidget);
  });

  testWidgets('Activity is a tab with a part for each thing the customer has set up, over Today, Yesterday, 7 and 30 days', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('Activity').last);
    await t.pumpAndSettle();
    for (final x in ['People', 'Vehicles', 'Areas', 'Health']) {
      expect(find.widgetWithText(Segments<Section>, x), findsOneWidget, reason: x);
    }
    // one tidy row of pull-down filters: the period, and (when there is more than one) the camera; the exact dates are written under them
    expect(find.widgetWithText(FilterMenu<Period>, 'Today'), findsOneWidget);
    expect(find.widgetWithText(FilterMenu<String?>, 'All cameras'), findsOneWidget);
    expect(find.text(periodRange(Period.today)), findsOneWidget);
    expect(find.text('Yesterday'), findsNothing, reason: 'the other choices are in the menu');
    expect(find.text('People in'), findsOneWidget);
    expect(find.text('342'), findsOneWidget);
    await t.tap(find.widgetWithText(FilterMenu<Period>, 'Today'));
    await t.pumpAndSettle();
    for (final x in ['Today', 'Yesterday', '7 days', '30 days']) {
      expect(find.text(x), findsWidgets, reason: x);
    }
    await t.tap(find.text('Yesterday').last);
    await t.pumpAndSettle();
    expect(find.text('410'), findsOneWidget, reason: 'yesterday\'s count');
    expect(find.text(periodRange(Period.yesterday)), findsOneWidget);
    await t.tap(find.widgetWithText(FilterMenu<Period>, 'Yesterday'));
    await t.pumpAndSettle();
    await t.tap(find.text('7 days').last);
    await t.pumpAndSettle();
    expect(find.textContaining('by day', findRichText: true), findsOneWidget);
    expect(find.textContaining(' – '), findsOneWidget, reason: 'a stretch of days shows both ends');
    await t.tap(find.text('Areas'));
    await t.pumpAndSettle();
    expect(find.text('Long queue'), findsOneWidget);
    expect(find.text('Billing queue'), findsOneWidget);
    expect(find.text('Too many people in an area'), findsNothing, reason: 'only the area alerts that are on');
    await t.tap(find.text('Health'));
    await t.pumpAndSettle();
    expect(find.text('Cameras working'), findsOneWidget);
    expect(find.textContaining('Went offline'.toUpperCase()), findsOneWidget);
    expect(find.text('Stockroom'), findsOneWidget);
  });

  testWidgets('the camera filter narrows People to one camera', (t) async {
    await phone(t);
    final repo = _CameraAskRepo();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('Activity').last);
    await t.pumpAndSettle();
    expect(repo.asked.last, isNull, reason: 'all cameras to begin with');
    await t.tap(find.widgetWithText(FilterMenu<String?>, 'All cameras'));
    await t.pumpAndSettle();
    await t.tap(find.text('Cash counter').last);
    await t.pumpAndSettle();
    expect(repo.asked.last, 'c2');
    expect(find.widgetWithText(FilterMenu<String?>, 'Cash counter'), findsOneWidget);
    await t.tap(find.text('Vehicles'));
    await t.pumpAndSettle();
    expect(find.byType(FilterMenu<String?>), findsNothing, reason: 'only one camera reads plates, so there is nothing to choose, and the old choice is dropped');
  });

  testWidgets('Activity shows only the parts the customer has set up', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: _SetupRepo(footfall: false, plates: false, smartOn: false)));
    await t.pumpAndSettle();
    await t.tap(find.text('Activity').last);
    await t.pumpAndSettle();
    expect(find.byType(Segments<Section>), findsNothing, reason: 'only Health is left, so there is nothing to choose between');
    expect(find.text('Cameras working'), findsOneWidget);
    await t.pumpWidget(IVisionApp(key: UniqueKey(), repo: _SetupRepo(footfall: true, plates: false, smartOn: true)));
    await t.pumpAndSettle();
    await t.tap(find.text('Activity').last);
    await t.pumpAndSettle();
    expect(find.widgetWithText(Segments<Section>, 'People'), findsOneWidget);
    expect(find.widgetWithText(Segments<Section>, 'Areas'), findsOneWidget);
    expect(find.descendant(of: find.byType(Segments<Section>), matching: find.text('Vehicles')), findsNothing);
  });

  testWidgets('Vehicles lists every plate read, searches, and the owner can add one to the blocked list and manage that list', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('Activity').last);
    await t.pumpAndSettle();
    await t.tap(find.text('Vehicles'));
    await t.pumpAndSettle();
    expect(find.text('GJ14AB1234'), findsOneWidget);
    expect(find.text('Needs a check'), findsOneWidget);
    expect(find.text('Blocked list'), findsOneWidget, reason: 'the pill on the plate that is on the list');
    expect(find.byTooltip('Blocked list'), findsOneWidget, reason: 'and the button to the list, beside the search');
    await t.enterText(find.byType(TextField), 'GJ14');
    await t.pump(const Duration(milliseconds: 500));
    await t.pumpAndSettle();
    expect(find.text('GJ05CD9876'), findsNothing);
    await t.tap(find.text('GJ14AB1234'));
    await t.pumpAndSettle();
    expect(find.text('Number plate'), findsOneWidget);
    await t.tap(find.text('Add to blocked list'));
    await t.pumpAndSettle();
    await t.enterText(find.widgetWithText(TextField, 'Note (optional)'), 'followed a customer');
    await t.tap(find.widgetWithText(FilledButton, 'Add to blocked list').last);
    await t.pumpAndSettle();
    expect(repo.blocked.map((b) => b.plate), contains('GJ14AB1234'));
    expect(find.text('GJ14AB1234 is on the blocked list.'), findsOneWidget);
    expect(find.text('Remove from blocked list'), findsOneWidget);
    await t.pageBack();
    await t.pumpAndSettle();
    await t.tap(find.byTooltip('Blocked list'));
    await t.pumpAndSettle();
    expect(find.text('GJ14AB1234'), findsOneWidget);
    expect(find.textContaining('followed a customer'), findsOneWidget);
    // add one by hand: a wrong plate says so, a right one is added
    await t.tap(find.text('Add a plate'));
    await t.pumpAndSettle();
    await t.enterText(find.widgetWithText(TextField, 'Plate number'), 'XX');
    await t.tap(find.widgetWithText(FilledButton, 'Add to blocked list'));
    await t.pumpAndSettle();
    expect(find.textContaining('not a valid Indian plate'), findsOneWidget);
    await t.enterText(find.widgetWithText(TextField, 'Plate number'), 'MH12AB1234');
    await t.tap(find.widgetWithText(FilledButton, 'Add to blocked list'));
    await t.pumpAndSettle();
    expect(find.text('MH12AB1234'), findsOneWidget);
    // take one off
    await t.tap(find.byTooltip('Take MH12AB1234 off the list'));
    await t.pumpAndSettle();
    await t.tap(find.text('Take it off'));
    await t.pumpAndSettle();
    expect(find.text('MH12AB1234'), findsNothing);
    expect(repo.blocked.map((b) => b.plate), isNot(contains('MH12AB1234')));
  });

  testWidgets('camera tiles show the latest picture and fetch it again every 5 seconds, two at a time', (t) async {
    await phone(t);
    final repo = _PictureRepo();
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: Scaffold(body: Column(children: [for (var i = 0; i < 4; i++) SizedBox(height: 60, child: CameraPicture(repo: repo, cameraId: 'c$i', active: true))]))));
    await t.pump();
    expect(repo.maxAtOnce, lessThanOrEqualTo(2), reason: 'never more than two pictures asked for at once');
    await t.pump(const Duration(milliseconds: 400));
    expect(repo.calls, 4, reason: 'every tile has its first picture');
    await t.pump(const Duration(seconds: 5));
    await t.pump(const Duration(milliseconds: 400));
    expect(repo.calls, 8, reason: 'and again after 5 seconds');
    expect(repo.maxAtOnce, lessThanOrEqualTo(2));
    await t.pumpWidget(const SizedBox());       // leaving the screen stops the refresh
    await t.pump(const Duration(seconds: 12));
    expect(repo.calls, 8);
  });

  testWidgets('an offline camera tile fetches nothing', (t) async {
    final repo = _PictureRepo();
    await t.pumpWidget(MaterialApp(home: Scaffold(body: SizedBox(height: 60, child: CameraPicture(repo: repo, cameraId: 'c0', active: false)))));
    await t.pump(const Duration(seconds: 11));
    expect(repo.calls, 0);
  });

  testWidgets('Reports shows scheduled reports, switches one off, and can schedule a new one', (t) async {
    await phone(t);
    final repo = MockRepository();
    await t.pumpWidget(IVisionApp(repo: repo));
    await t.pumpAndSettle();
    await t.tap(find.text('More').last);
    await t.pumpAndSettle();
    expect(find.text('Your plan, people and recorders'), findsOneWidget);
    expect(find.text('Settings'), findsOneWidget);
    await t.tap(find.text('Reports'));
    await t.pumpAndSettle();
    expect(find.text('Sent on WhatsApp on a schedule'.toUpperCase()), findsOneWidget);
    expect(find.textContaining('Every day at 9 pm'), findsOneWidget);
    await t.tap(find.byType(Switch).first);
    await t.pumpAndSettle();
    expect((await repo.schedules()).first.enabled, isFalse);
    await t.tap(find.text('Schedule'));
    await t.pumpAndSettle();
    expect(find.text('Which report'.toUpperCase()), findsOneWidget);
    await t.scrollUntilVisible(find.text('Save'), 300, scrollable: find.byType(Scrollable).first);
    expect(tester(t, 'Save').onPressed, isNull, reason: 'someone must get it');
    await t.scrollUntilVisible(find.text('Vehicle audit'), -300, scrollable: find.byType(Scrollable).first);
    await t.tap(find.text('Vehicle audit'));
    await t.pump();
    await t.scrollUntilVisible(find.textContaining('98•••• 3210'), 300, scrollable: find.byType(Scrollable).first);
    await t.tap(find.textContaining('98•••• 3210'));
    await t.pump();
    await t.scrollUntilVisible(find.text('Save'), 300, scrollable: find.byType(Scrollable).first);
    expect(tester(t, 'Save').onPressed, isNotNull);
    await t.tap(find.text('Save'));
    await t.pumpAndSettle();
    expect((await repo.schedules()).length, 4);
  });

  test('an alert reads as one plain sentence, never raw setting names', () {
    AlertDef d(String type, List<Setting> st, Map<String, num> params) => AlertDef(type: type, title: type, description: 'Plain description.', smart: true, settings: st, enabled: true, params: params);
    Setting w(String key, int def) => Setting(key, settingWords[key]!.$1, settingWords[key]!.$2, 1, 100, def);
    expect(d('crowding', [w('people', 50)], {'people': 50}).summary, 'Alert when more than 50 people inside');
    expect(d('vehicle_overstay', [w('max_hours', 8)], {'max_hours': 8}).summary, 'Alert when a vehicle stays more than 8 hours');
    expect(d('vehicle_overstay', [w('max_minutes', 45)], {'max_minutes': 45}).summary, 'Alert when a vehicle stays more than 45 minutes');
    expect(d('queue_long', [w('length', 6), w('minutes', 5)], {'length': 6, 'minutes': 5}).summary, 'Alert when more than 6 people waiting, for at least 5 minutes');
    expect(d('queue_long', [w('length', 6), w('minutes', 5), w('picture_minutes', 2)], {'length': 6, 'minutes': 5, 'picture_minutes': 2}).summary, 'Alert when more than 6 people waiting, for at least 5 minutes', reason: 'the picture interval is set in the editor, not read out');
    expect(settingWords['picture_minutes']!.$1, 'Take another picture every');
    expect(d('connection_unstable', [w('drops', 5)], {'drops': 5}).summary, 'Alert after 5 drops in an hour');
    expect(d('daily_summary', [w('hour', 8)], {'hour': 8}).summary, 'Send it at 8 am');
    expect(d('blocked_plate', [], {}).summary, 'Plain description.');
  });

  test('repeats of the same alert in the same place fold into one row, newest first', () {
    final now = DateTime(2026, 11, 10, 18);
    AlertItem a(String id, String rule, String where, int hoursAgo, {AlertState st = AlertState.open}) => AlertItem(id: id, title: 'Occupancy limit exceeded', body: '', rule: rule, where: where, when: '', at: now.subtract(Duration(hours: hoursAgo)), state: st, smart: true);
    final groups = groupAlerts([a('1', 'people_in_area', 'Aisle 1', 5), a('2', 'people_in_area', 'Aisle 1', 1), a('3', 'people_in_area', 'Aisle 2', 3), a('4', 'camera_offline', 'Aisle 1', 2)]);
    expect(groups.length, 3);
    expect(groups.first.latest.id, '2');
    expect(groups.first.count, 2);
    expect(repeatsLine(groups.first), startsWith('Aisle 1 · 2 times, last'));
    expect(isRecent(a('9', 'x', 'y', 30), now: now), isFalse);
    expect(isRecent(a('9', 'x', 'y', 3), now: now), isTrue);
  });
}

class _FakePush implements PushService {
  _FakePush(this._t, this.platform, {bool granted = false, this.supported = true, this.hint = ''}) : granted_ = granted;
  final String? _t;
  final bool granted_;
  int asked = 0;       // how many times a permission box would have opened
  @override
  final String platform;
  @override
  final bool supported;
  @override
  final String hint;
  @override
  Future<bool> granted() async => granted_;
  @override
  Future<String?> token({bool ask = false}) async {
    if (ask) asked++;
    return granted_ || ask ? _t : null;
  }
}

class _FakeBrowser extends Browser {
  _FakeBrowser({String path = '/', Map<String, String> query = const {}, this.openedByStaff = false, this.grant}) : _path = path, _query = query;
  final String _path;
  final Map<String, String> _query;
  @override
  final bool openedByStaff;
  final ViewAsGrant? grant;
  String? handedOff, stopped;
  String url = '';
  @override
  String get path => _path;
  @override
  Map<String, String> get query => _query;
  @override
  void replaceUrl(String path) => url = path;
  @override
  void handOffToStaff(String refreshToken) => handedOff = refreshToken;
  @override
  Future<ViewAsGrant?> waitForViewAs({Duration timeout = const Duration(seconds: 10)}) async => grant;
  @override
  void stopViewAs(String sessionId) => stopped = sessionId;
}

/// Hands out a tiny picture after a short wait, and counts how many were asked for at once.
class _PictureRepo extends MockRepository {
  int calls = 0, now = 0, maxAtOnce = 0;
  static final _png = base64Decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==');
  @override
  Future<Uint8List?> snapshot(String cameraId) async {
    calls++;
    now++;
    if (now > maxAtOnce) maxAtOnce = now;
    await Future<void>.delayed(const Duration(milliseconds: 100));
    now--;
    return _png;
  }
}

/// A customer who has set up only some things: which cameras count people or read plates, and whether the smart alerts are on.
class _SetupRepo extends MockRepository {
  _SetupRepo({required this.footfall, required bool plates, required this.smartOn}) : readsPlates = plates;
  final bool footfall, readsPlates, smartOn;
  @override
  Future<List<CameraItem>> cameras() async => [CameraItem('c1', 'Front door', 'Store recorder', true, useFootfall: footfall, useAnpr: readsPlates)];
  @override
  Future<List<AlertDef>> alertDefs() async {
    final all = await super.alertDefs();
    for (final d in all) {
      if (d.smart) d.enabled = smartOn;
    }
    return all;
  }
}

/// Remembers which camera Activity asked the people counts for.
class _CameraAskRepo extends MockRepository {
  final asked = <String?>[];
  @override
  Future<FootfallRange> footfallRange({required Period period, String? cameraId}) {
    asked.add(cameraId);
    return super.footfallRange(period: period, cameraId: cameraId);
  }
}
