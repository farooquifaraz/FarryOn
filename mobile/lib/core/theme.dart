import 'package:flutter/material.dart';

/// "Midnight Aurora" — FarryOn's dark, premium, voice-first design system.
///
/// A single source of truth for the app's colours, surfaces, and the Material
/// [ThemeData]. Widgets pull semantic colours from here (or from the derived
/// [ColorScheme]) so the look stays consistent and easy to retune.
class Aurora {
  Aurora._();

  // -- Surfaces (near-black, slightly blue) --------------------------------
  static const Color base = Color(0xFF0B0E14); // scaffold background
  static const Color surface = Color(0xFF10141B); // panels / camera well
  static const Color surfaceHigh = Color(0xFF161B24); // raised cards

  // -- Accents -------------------------------------------------------------
  static const Color teal = Color(0xFF1D9E75); // primary / mic / online
  static const Color mint = Color(0xFF5DCAA5); // brighter teal highlight
  static const Color tealInk = Color(0xFF04342C); // text on teal fills
  static const Color purple = Color(0xFF7F77DD); // assistant / secondary
  static const Color purpleSoft = Color(0xFFAFA9EC); // assistant labels
  static const Color amber = Color(0xFFEF9F27); // thinking
  static const Color danger = Color(0xFFE24B4A); // errors / barge-in

  // -- Text ----------------------------------------------------------------
  static const Color textPrimary = Color(0xFFE8EAED);
  static const Color textMuted = Color(0xFF8A9099);

  // -- "Glass" overlays (translucent white over the dark base) -------------
  static const Color glass = Color(0x12FFFFFF); // ~7% white fill
  static const Color glassStrong = Color(0x1FFFFFFF); // ~12% white fill
  static const Color glassBorder = Color(0x1AFFFFFF); // ~10% white border

  /// Translucent tint of an accent for soft status pills / chips.
  static Color tint(Color c, [double opacity = 0.16]) =>
      c.withValues(alpha: opacity);

  // -- Gradients -----------------------------------------------------------
  // The redesign keeps every colour above but evolves flat fills into soft
  // two/three-stop gradients. `primary` fills the main CTA + mic; the category
  // gradients tint the settings icon glyphs (via [GradientIcon]) so each group
  // reads at a glance. Angles ≈ 135° (topLeft → bottomRight).

  /// Primary call-to-action / mic fill (teal → mint). Ink text = [tealInk].
  static const LinearGradient primaryGradient = LinearGradient(
    begin: Alignment.centerLeft,
    end: Alignment.centerRight,
    colors: [Color(0xFF0F6E56), Color(0xFF1D9E75), Color(0xFF5DCAA5)],
  );

  static const LinearGradient gradTeal = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF1D9E75), Color(0xFF5DCAA5)],
  );
  static const LinearGradient gradPurple = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF7F77DD), Color(0xFFAFA9EC)],
  );
  static const LinearGradient gradBlue = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF378ADD), Color(0xFF85B7EB)],
  );
  static const LinearGradient gradCoral = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFD85A30), Color(0xFFF0997B)],
  );
  static const LinearGradient gradAmber = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFEF9F27), Color(0xFFFAC775)],
  );
  static const LinearGradient gradGreen = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF639922), Color(0xFF97C459)],
  );
  static const LinearGradient gradPink = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFD4537E), Color(0xFFED93B1)],
  );

  /// The assembled dark theme.
  static ThemeData theme() {
    final scheme = ColorScheme.fromSeed(
      seedColor: teal,
      brightness: Brightness.dark,
    ).copyWith(
      primary: teal,
      onPrimary: tealInk,
      secondary: purple,
      surface: surface,
      onSurface: textPrimary,
      error: danger,
      outline: textMuted,
    );

    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: Aurora.base,
    );

    return base.copyWith(
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          color: textPrimary,
          fontSize: 20,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.3,
        ),
        iconTheme: IconThemeData(color: textMuted),
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: surfaceHigh,
        surfaceTintColor: Colors.transparent,
      ),
      textTheme: base.textTheme.apply(
        bodyColor: textPrimary,
        displayColor: textPrimary,
      ),
      snackBarTheme: const SnackBarThemeData(
        backgroundColor: surfaceHigh,
        contentTextStyle: TextStyle(color: textPrimary),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
}
