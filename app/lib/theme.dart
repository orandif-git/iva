import 'package:flutter/material.dart';

/// iVision Clear: the colours mean something. Blue = you can act, green = fine, amber = look soon, red = fix now, violet = the AI is involved.
class Tok {
  final Color bg, surface, surface2, line, fg, fg2, brand, brandSoft, okInk, okSoft, warnInk, warnSoft, crit, critSoft, ai, aiSoft;
  const Tok({required this.bg, required this.surface, required this.surface2, required this.line, required this.fg, required this.fg2, required this.brand,
      required this.brandSoft, required this.okInk, required this.okSoft, required this.warnInk, required this.warnSoft, required this.crit, required this.critSoft,
      required this.ai, required this.aiSoft});

  static const light = Tok(
      bg: Color(0xFFF4F6F9), surface: Color(0xFFFFFFFF), surface2: Color(0xFFF0F3F8), line: Color(0xFFE1E6EE), fg: Color(0xFF0F1A2B), fg2: Color(0xFF506078),
      brand: Color(0xFF1F5EE6), brandSoft: Color(0xFFE3ECFD), okInk: Color(0xFF0B6B3A), okSoft: Color(0xFFDDF4E6), warnInk: Color(0xFF7A4700), warnSoft: Color(0xFFFFF0D1),
      crit: Color(0xFFC42B20), critSoft: Color(0xFFFDE4E1), ai: Color(0xFF5B3DE0), aiSoft: Color(0xFFEEEAFF));
  static const dark = Tok(
      bg: Color(0xFF0A1018), surface: Color(0xFF121B27), surface2: Color(0xFF182433), line: Color(0xFF243447), fg: Color(0xFFE8EEF6), fg2: Color(0xFF97A9BD),
      brand: Color(0xFF6EA2FF), brandSoft: Color(0xFF16284A), okInk: Color(0xFF56D390), okSoft: Color(0xFF10301F), warnInk: Color(0xFFF2B057), warnSoft: Color(0xFF352610),
      crit: Color(0xFFFF8A7D), critSoft: Color(0xFF3A1915), ai: Color(0xFFB5A2FF), aiSoft: Color(0xFF251E4D));

  static Tok of(BuildContext c) => Theme.of(c).brightness == Brightness.dark ? dark : light;
}

ThemeData buildTheme(Brightness b) {
  final t = b == Brightness.dark ? Tok.dark : Tok.light;
  final scheme = ColorScheme.fromSeed(seedColor: t.brand, brightness: b).copyWith(
      primary: t.brand, onPrimary: b == Brightness.dark ? const Color(0xFF08101C) : Colors.white, surface: t.surface, onSurface: t.fg, error: t.crit, outline: t.line);
  return ThemeData(
    useMaterial3: true,
    brightness: b,
    colorScheme: scheme,
    scaffoldBackgroundColor: t.bg,
    cardTheme: CardThemeData(color: t.surface, elevation: 0, margin: EdgeInsets.zero, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: t.line))),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(minimumSize: const Size(44, 48), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(minimumSize: const Size(44, 48), side: BorderSide(color: t.line), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)))),
    appBarTheme: AppBarTheme(backgroundColor: t.surface, foregroundColor: t.fg, elevation: 0, scrolledUnderElevation: 0),
    navigationBarTheme: NavigationBarThemeData(backgroundColor: t.surface, indicatorColor: t.brandSoft),
    navigationRailTheme: NavigationRailThemeData(backgroundColor: t.surface, indicatorColor: t.brandSoft),
    dividerColor: t.line,
  );
}
