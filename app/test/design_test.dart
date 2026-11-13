import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:ivision_app/main.dart';
import 'package:ivision_app/repo.dart';
import 'package:ivision_app/screens/common.dart';
import 'package:ivision_app/theme.dart';

import '../tool/check_design.dart' as guard;

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('screens use the shared colour and corner tokens', () {
    final found = guard.violations(Directory('${Directory.current.path}/lib'));
    expect(found, isEmpty, reason: '${found.length} hand-written values:\n${found.join('\n')}');
  });

  testWidgets('figures are tabular, so a changing count does not shift', (t) async {
    await t.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: const Scaffold(body: StatStrip([('People in', '12'), ('Open alerts', '3')]))));
    final figure = t.widget<Text>(find.text('12'));
    expect(figure.style?.fontFeatures, contains(const FontFeature.tabularFigures()));
  });

  for (final size in const [Size(390, 844), Size(1200, 900)]) {
    testWidgets('AI Watch fits at ${size.width.toInt()} wide', (t) async {
      t.view.physicalSize = size * 2;
      t.view.devicePixelRatio = 2;
      addTearDown(t.view.reset);
      await t.pumpWidget(IVisionApp(repo: MockRepository()));
      await t.pumpAndSettle();
      await t.tap(find.text('AI Watch').last);
      await t.pumpAndSettle();
      final smart = find.textContaining('Smart alerts (');
      await t.ensureVisible(smart);
      await t.tap(smart);
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
    });
  }
}
