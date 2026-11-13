import 'package:flutter/material.dart';

/// The Jas iVision design system (docs/DESIGN.md is the rule book; every screen follows it).
/// The colours mean something. Blue = you can act, green = fine, amber = look soon, red = fix now, violet = the AI is involved.
class Tok {
  final Color bg, surface, surface2, line, fg, fg2, brand, accent, brandDeep, brandSoft, okInk, okSoft, warnInk, warnSoft, crit, critSoft, ai, aiSoft, shadow, picture;
  const Tok({required this.bg, required this.surface, required this.surface2, required this.line, required this.fg, required this.fg2, required this.brand, required this.accent, required this.brandDeep,
      required this.brandSoft, required this.okInk, required this.okSoft, required this.warnInk, required this.warnSoft, required this.crit, required this.critSoft,
      required this.ai, required this.aiSoft, required this.shadow, required this.picture});

  static const light = Tok(
      bg: Color(0xFFF4F6FA), surface: Color(0xFFFFFFFF), surface2: Color(0xFFE9EDF4), line: Color(0xFFE3E8F0), fg: Color(0xFF0E1726), fg2: Color(0xFF55627A),
      brand: Color(0xFF0B6FB8), accent: Color(0xFF138BE0), brandDeep: Color(0xFF0A3D73), brandSoft: Color(0xFFE6F1FB), okInk: Color(0xFF0B6B3A), okSoft: Color(0xFFDDF4E6), warnInk: Color(0xFF7A4700), warnSoft: Color(0xFFFFF0D1),
      crit: Color(0xFFB42318), critSoft: Color(0xFFFDE7E4), ai: Color(0xFF5B45D6), aiSoft: Color(0xFFEEEAFE), shadow: Color(0x240E1726), picture: Color(0xFF1B2A41));
  static const dark = Tok(
      bg: Color(0xFF080E16), surface: Color(0xFF111A26), surface2: Color(0xFF182433), line: Color(0xFF243447), fg: Color(0xFFE8EEF6), fg2: Color(0xFF9AACC0),
      brand: Color(0xFF5FB4F5), accent: Color(0xFF3BA3F0), brandDeep: Color(0xFF0C1F3D), brandSoft: Color(0xFF16284A), okInk: Color(0xFF56D390), okSoft: Color(0xFF10301F), warnInk: Color(0xFFF2B057), warnSoft: Color(0xFF352610),
      crit: Color(0xFFFF8A7D), critSoft: Color(0xFF3A1915), ai: Color(0xFFB5A2FF), aiSoft: Color(0xFF251E4D), shadow: Color(0x66000000), picture: Color(0xFF0A1018));

  static Tok of(BuildContext c) => Theme.of(c).brightness == Brightness.dark ? dark : light;

  List<BoxShadow> get lift => [BoxShadow(color: shadow, blurRadius: 24, offset: const Offset(0, 8)), BoxShadow(color: shadow, blurRadius: 2, offset: const Offset(0, 1))];
}

/// Corner sizes. A card is 22, a small card or tile 18, a button 16, a small control 14, a picture inside a card 14, a sheet 32.
class Radii {
  static const card = 22.0, tile = 18.0, button = 16.0, control = 14.0, picture = 14.0, sheet = 32.0, chip = 12.0, pill = 99.0, bar = 34.0;
}

/// How things move: short, soft, and never in the way. All of it stops when the device asks for less motion (see widgets/motion.dart).
class Motion {
  static const rise = Duration(milliseconds: 320), quick = Duration(milliseconds: 220), chart = Duration(milliseconds: 500);
  static const ease = Cubic(.2, .8, .2, 1);
}

/// Headings and big numbers: Space Grotesk, bold, pulled a little tight.
TextStyle display(double size, {Color? color}) => TextStyle(fontFamily: 'SpaceGrotesk', fontFamilyFallback: const ['Inter'], fontSize: size, fontWeight: FontWeight.w700, letterSpacing: size >= 24 ? -.6 : -.3, height: 1.15, color: color);

/// A figure that changes while you watch (a count, a clock, a percentage). Tabular digits keep it from shifting sideways.
TextStyle figure(double size, {Color? color}) => display(size, color: color).copyWith(fontFeatures: const [FontFeature.tabularFigures()]);

ThemeData buildTheme(Brightness b) {
  final t = b == Brightness.dark ? Tok.dark : Tok.light;
  final scheme = ColorScheme.fromSeed(seedColor: t.brand, brightness: b).copyWith(
      primary: t.brand, onPrimary: b == Brightness.dark ? const Color(0xFF08101C) : Colors.white, surface: t.surface, onSurface: t.fg, error: t.crit, outline: t.line, surfaceTint: Colors.transparent);
  // A ripple, not Material 3's sparkle: the sparkle is a shader that some renderers cannot compile, and a calm ripple suits an
  // operations console better than a decorative flourish.
  final base = ThemeData(useMaterial3: true, brightness: b, colorScheme: scheme, fontFamily: 'Inter', splashFactory: InkRipple.splashFactory);
  final text = base.textTheme.apply(bodyColor: t.fg, displayColor: t.fg).copyWith(
        titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.3, color: t.fg),
        titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600, color: t.fg),
        bodyMedium: base.textTheme.bodyMedium?.copyWith(height: 1.45, color: t.fg),
      );
  return base.copyWith(
    textTheme: text,
    scaffoldBackgroundColor: t.bg,
    cardTheme: CardThemeData(color: t.surface, elevation: 0, margin: EdgeInsets.zero, shadowColor: t.shadow, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.card), side: BorderSide(color: t.line))),
    filledButtonTheme: FilledButtonThemeData(style: FilledButton.styleFrom(minimumSize: const Size(44, 50), padding: const EdgeInsets.symmetric(horizontal: 22), textStyle: const TextStyle(fontFamily: 'Inter', fontWeight: FontWeight.w600, fontSize: 15.5), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.button)))),
    outlinedButtonTheme: OutlinedButtonThemeData(style: OutlinedButton.styleFrom(minimumSize: const Size(44, 50), foregroundColor: t.fg, backgroundColor: t.surface, padding: const EdgeInsets.symmetric(horizontal: 20), textStyle: const TextStyle(fontFamily: 'Inter', fontWeight: FontWeight.w600, fontSize: 15.5), side: BorderSide(color: t.line), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radii.button)))),
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
    appBarTheme: AppBarTheme(backgroundColor: t.surface, foregroundColor: t.fg, elevation: 0, scrolledUnderElevation: 0, centerTitle: false, titleTextStyle: display(19, color: t.fg)),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: t.surface,
      height: 72,
      indicatorColor: t.brandSoft,
      indicatorShape: const StadiumBorder(),
      labelTextStyle: WidgetStatePropertyAll(TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: t.fg2)),
    ),
    tabBarTheme: TabBarThemeData(labelStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), unselectedLabelStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15), dividerColor: t.line),
    floatingActionButtonTheme: FloatingActionButtonThemeData(backgroundColor: t.brand, foregroundColor: scheme.onPrimary, elevation: 6, extendedTextStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18))),
    switchTheme: SwitchThemeData(
      trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent),
      thumbColor: const WidgetStatePropertyAll(Colors.white),
      trackColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? t.brand : (b == Brightness.dark ? const Color(0xFF3A4A5E) : const Color(0xFFC5CCD9))),
    ),
    dividerColor: t.line,
    snackBarTheme: SnackBarThemeData(behavior: SnackBarBehavior.floating, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14))),
  );
}
