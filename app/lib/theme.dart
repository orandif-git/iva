import 'package:flutter/material.dart';

/// iVision Clear: the colours mean something. Blue = you can act, green = fine, amber = look soon, red = fix now, violet and cyan = the AI is involved.
class Tok {
  final Color bg, surface, surface2, line, fg, fg2, brand, brandDeep, brandSoft, okInk, okSoft, warnInk, warnSoft, crit, critSoft, ai, aiSoft, shadow;
  const Tok({required this.bg, required this.surface, required this.surface2, required this.line, required this.fg, required this.fg2, required this.brand, required this.brandDeep,
      required this.brandSoft, required this.okInk, required this.okSoft, required this.warnInk, required this.warnSoft, required this.crit, required this.critSoft,
      required this.ai, required this.aiSoft, required this.shadow});

  static const light = Tok(
      bg: Color(0xFFF2F5FA), surface: Color(0xFFFFFFFF), surface2: Color(0xFFEDF1F8), line: Color(0xFFE0E6F0), fg: Color(0xFF0B1220), fg2: Color(0xFF52627A),
      brand: Color(0xFF1F5EE6), brandDeep: Color(0xFF0A3D73), brandSoft: Color(0xFFE4EDFE), okInk: Color(0xFF0B6B3A), okSoft: Color(0xFFDDF4E6), warnInk: Color(0xFF7A4700), warnSoft: Color(0xFFFFF0D1),
      crit: Color(0xFFC42B20), critSoft: Color(0xFFFDE4E1), ai: Color(0xFF5B3DE0), aiSoft: Color(0xFFEEEAFF), shadow: Color(0x140B1B3A));
  static const dark = Tok(
      bg: Color(0xFF080E16), surface: Color(0xFF111A26), surface2: Color(0xFF182433), line: Color(0xFF243447), fg: Color(0xFFE8EEF6), fg2: Color(0xFF9AACC0),
      brand: Color(0xFF6EA2FF), brandDeep: Color(0xFF0C1F3D), brandSoft: Color(0xFF16284A), okInk: Color(0xFF56D390), okSoft: Color(0xFF10301F), warnInk: Color(0xFFF2B057), warnSoft: Color(0xFF352610),
      crit: Color(0xFFFF8A7D), critSoft: Color(0xFF3A1915), ai: Color(0xFFB5A2FF), aiSoft: Color(0xFF251E4D), shadow: Color(0x66000000));

  static Tok of(BuildContext c) => Theme.of(c).brightness == Brightness.dark ? dark : light;

  List<BoxShadow> get lift => [BoxShadow(color: shadow, blurRadius: 24, offset: const Offset(0, 8)), BoxShadow(color: shadow, blurRadius: 2, offset: const Offset(0, 1))];
}

ThemeData buildTheme(Brightness b) {
  final t = b == Brightness.dark ? Tok.dark : Tok.light;
  final scheme = ColorScheme.fromSeed(seedColor: t.brand, brightness: b).copyWith(
      primary: t.brand, onPrimary: b == Brightness.dark ? const Color(0xFF08101C) : Colors.white, surface: t.surface, onSurface: t.fg, error: t.crit, outline: t.line, surfaceTint: Colors.transparent);
  final base = ThemeData(useMaterial3: true, brightness: b, colorScheme: scheme, fontFamily: 'Inter');
  final text = base.textTheme.apply(bodyColor: t.fg, displayColor: t.fg).copyWith(
        titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.3, color: t.fg),
        titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600, color: t.fg),
        bodyMedium: base.textTheme.bodyMedium?.copyWith(height: 1.45, color: t.fg),
      );
  return base.copyWith(
    textTheme: text,
    scaffoldBackgroundColor: t.bg,
    cardTheme: CardThemeData(color: t.surface, elevation: 0, margin: EdgeInsets.zero, shadowColor: t.shadow, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide(color: t.line))),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(minimumSize: const Size(44, 48), padding: const EdgeInsets.symmetric(horizontal: 22), textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(minimumSize: const Size(44, 48), foregroundColor: t.fg, padding: const EdgeInsets.symmetric(horizontal: 20), textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15), side: BorderSide(color: t.line, width: 1.5), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)))),
    textButtonTheme: TextButtonThemeData(style: TextButton.styleFrom(minimumSize: const Size(44, 44), textStyle: const TextStyle(fontWeight: FontWeight.w600))),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: t.surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: t.line, width: 1.5)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: t.line, width: 1.5)),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: t.brand, width: 2)),
    ),
    segmentedButtonTheme: SegmentedButtonThemeData(style: ButtonStyle(
      side: WidgetStatePropertyAll(BorderSide(color: t.line, width: 1.5)),
      shape: WidgetStatePropertyAll(RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
      textStyle: const WidgetStatePropertyAll(TextStyle(fontWeight: FontWeight.w600)),
    )),
    appBarTheme: AppBarTheme(backgroundColor: t.surface, foregroundColor: t.fg, elevation: 0, scrolledUnderElevation: 0, centerTitle: false, titleTextStyle: TextStyle(fontFamily: 'Inter', fontSize: 19, fontWeight: FontWeight.w700, color: t.fg)),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: t.surface,
      height: 72,
      indicatorColor: t.brandSoft,
      indicatorShape: const StadiumBorder(),
      labelTextStyle: WidgetStatePropertyAll(TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: t.fg2)),
    ),
    tabBarTheme: TabBarThemeData(labelStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), unselectedLabelStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15), dividerColor: t.line),
    floatingActionButtonTheme: FloatingActionButtonThemeData(backgroundColor: t.brand, foregroundColor: scheme.onPrimary, elevation: 6, extendedTextStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18))),
    switchTheme: SwitchThemeData(trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent)),
    dividerColor: t.line,
    snackBarTheme: SnackBarThemeData(behavior: SnackBarBehavior.floating, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
  );
}
