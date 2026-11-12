// Guards the design system in docs/DESIGN.md: a screen may not invent a colour or a card-sized corner.
//
// Run it before a release (and in CI): `dart run tool/check_design.dart`.
//
// Two kinds of file are allowed to hold raw values:
//  * theme.dart itself, which is where the tokens live;
//  * the artwork and video chrome that is not part of the interface — the brand mark, the sign-in painting, the camera
//    placeholder and the live-video overlays. Their colours are pictures, not meaning, so they are not tokens.
//
// Shapes smaller than a control (dots, bars, badges — 10 or less) may be written by hand: they are geometry, not corners.
import 'dart:io';

const artwork = {
  'lib/live/live_view.dart',
  'lib/screens/signin.dart',
  'lib/screens/wizard.dart',
  'lib/widgets/area_editor.dart',
  'lib/widgets/brand.dart',
  'lib/widgets/count_line_editor.dart',
};

final _hex = RegExp(r'Color\(0x[0-9A-Fa-f]+\)');
final _radius = RegExp(r'(?:Border)?Radius\.circular\((\d+(?:\.\d+)?)\)');

/// Every place a screen broke the rule, as `path:line: what`.
List<String> violations(Directory lib) {
  final found = <String>[];
  for (final f in lib.listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart'))) {
    final path = f.path.replaceAll('\\', '/');
    final rel = path.substring(path.indexOf('lib/'));
    final tokenFile = rel == 'lib/theme.dart';
    final art = artwork.contains(rel);
    final lines = f.readAsLinesSync();
    for (var i = 0; i < lines.length; i++) {
      final line = lines[i];
      if (!tokenFile && !art && _hex.hasMatch(line)) found.add('$rel:${i + 1}: write the colour as a Tok value (docs/DESIGN.md)');
      for (final m in _radius.allMatches(line)) {
        final size = double.parse(m.group(1)!);
        if (!tokenFile && size > 10) found.add('$rel:${i + 1}: corner $size must be a Radii value (docs/DESIGN.md)');
      }
    }
  }
  return found;
}

void main() {
  final root = File.fromUri(Platform.script).parent.parent;       // app/
  final found = violations(Directory('${root.path}/lib'));
  if (found.isEmpty) {
    stdout.writeln('Design check: every colour and card corner comes from theme.dart.');
    return;
  }
  stderr.writeln('Design check failed (${found.length}):');
  for (final v in found) {
    stderr.writeln('  $v');
  }
  exitCode = 1;
}
