import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ivision_app/api_client.dart';
import 'package:ivision_app/geometry/polygon.dart';
import 'package:ivision_app/main.dart';
import 'package:ivision_app/save_file.dart';
import 'package:ivision_app/screens/more.dart';
import 'package:ivision_app/screens/recorders.dart';
import 'package:ivision_app/theme.dart';
import 'package:ivision_app/models.dart';
import 'package:ivision_app/repo.dart';
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
    expect(find.bySemanticsLabel('Jas iVision'), findsWidgets);
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

  testWidgets('Alerts is only the inbox; what you watch for is in AI Watch, split into System and Smart', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('Alerts').last);
    await t.pumpAndSettle();
    expect(find.text('My alerts'), findsNothing, reason: 'no tabs: viewing and setting up are not mixed');
    expect(find.text('Needs you'), findsOneWidget);
    expect(find.text('History'), findsOneWidget);
    expect(find.textContaining('Watching for'), findsNothing, reason: 'no duplicate Manage card: AI Watch is its own tab');
    expect(find.text('Manage'), findsNothing);
    expect(find.text('New alert'), findsNothing, reason: 'the inbox has no set-up button');
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

  testWidgets('five tabs: Home, Cameras, AI Watch, Alerts, More, and they fit without overlapping', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    final bar = find.byType(AppTabBar);
    var last = -1.0;
    for (final x in ['Home', 'Cameras', 'AI Watch', 'Alerts', 'More']) {
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
    final bar = t.getRect(find.descendant(of: find.byType(AppTabBar), matching: find.byType(BackdropFilter)));
    expect(bar.left, greaterThan(8), reason: 'not edge to edge');
    expect(bar.right, lessThan(390 - 8));
    expect(bar.bottom, lessThan(844 - 4), reason: 'it hovers, it does not sit on the edge of the screen');
    expect(find.descendant(of: find.byType(AppTabBar), matching: find.byType(ClipRRect)), findsWidgets);
  });

  testWidgets('Cameras are grouped by recorder and tell you if they are all working', (t) async {
    await phone(t);
    await t.pumpWidget(IVisionApp(repo: MockRepository()));
    await t.pumpAndSettle();
    await t.tap(find.text('Cameras').last);
    await t.pumpAndSettle();
    expect(find.text('All 8 cameras are working'), findsOneWidget);
    expect(find.text('Store recorder'.toUpperCase()), findsOneWidget);
    expect(find.text('Godown recorder'.toUpperCase()), findsOneWidget);
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
    expect(find.text('connect.example.in'), findsOneWidget);
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
    expect(find.text('What is typed into the recorder'.toUpperCase()), findsOneWidget);
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
    expect(d('queue_long', [w('length', 6), w('minutes', 5)], {'length': 6, 'minutes': 5}).summary, 'Alert when more than 6 people waiting, for at least 5 minutes');
    expect(d('connection_unstable', [w('drops', 5)], {'drops': 5}).summary, 'Alert after 5 drops in an hour');
    expect(d('daily_summary', [w('hour', 8)], {'hour': 8}).summary, 'Send it at 8 am');
    expect(d('blocked_plate', [], {}).summary, 'Plain description.');
  });

  test('repeats of the same alert in the same place fold into one row, newest first', () {
    final now = DateTime(2026, 11, 10, 18);
    AlertItem a(String id, String rule, String where, int hoursAgo, {AlertState st = AlertState.open}) => AlertItem(id: id, title: 'Too many people', body: '', rule: rule, where: where, when: '', at: now.subtract(Duration(hours: hoursAgo)), state: st, smart: true);
    final groups = groupAlerts([a('1', 'people_in_area', 'Aisle 1', 5), a('2', 'people_in_area', 'Aisle 1', 1), a('3', 'people_in_area', 'Aisle 2', 3), a('4', 'camera_offline', 'Aisle 1', 2)]);
    expect(groups.length, 3);
    expect(groups.first.latest.id, '2');
    expect(groups.first.count, 2);
    expect(repeatsLine(groups.first), startsWith('Aisle 1 · 2 times, last'));
    expect(isRecent(a('9', 'x', 'y', 30), now: now), isFalse);
    expect(isRecent(a('9', 'x', 'y', 3), now: now), isTrue);
  });
}
