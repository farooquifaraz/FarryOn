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

  // -- Auth surfaces (splash / sign-in / sign-up) --------------------------
  // These screens run their own, brighter world: a vertical teal wash that
  // rises to a neon glow at the bottom, instead of the near-black [base] the
  // rest of the app sits on. It's the front door — the one place the brand
  // gets to be loud before the app settles into being a tool.
  static const Color neon = Color(0xFF00FFD1);

  /// The auth backdrop. Six stops so the climb from near-black to neon reads
  /// as light rising rather than a band.
  static const LinearGradient authBackdrop = LinearGradient(
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
    stops: [0.0, 0.30, 0.52, 0.68, 0.82, 1.0],
    colors: [
      Color(0xFF06140F),
      Color(0xFF0A2620),
      Color(0xFF0F3E33),
      Color(0xFF12564A),
      Color(0xFF1E7E68),
      Color(0xFF34C9A6),
    ],
  );

  /// The auth CTA fill — teal into blue, so the primary button separates
  /// itself from a backdrop that is teal all the way down.
  static const LinearGradient authCta = LinearGradient(
    begin: Alignment.centerLeft,
    end: Alignment.centerRight,
    colors: [Color(0xFF00D9A6), Color(0xFF12A6E0)],
  );

  /// Ink on the auth CTA / white buttons — near-black with a green cast, so
  /// it belongs to the palette rather than being a plain black.
  static const Color authInk = Color(0xFF0A1F1B);

  static const Color authFieldFill = Color(0x8C06140F); // ~55% of bg stop 1
  static const Color authFieldBorder = Color(0xFF28453F);
  static const Color authMutedFill = Color(0xA6132E29); // social button
  static const Color authMutedBorder = Color(0xFF26443E);
  static const Color authTextDim = Color(0xFF93B0A8);
  static const Color authTextFaint = Color(0xFF547069);

  /// Every auth button — CTA, white, social — is this tall with this radius.
  /// One spec, so two stacked buttons can never disagree.
  static const double authButtonHeight = 52;
  static const double authButtonRadius = 30;

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
  /// One rounded border, recoloured per state — so every field in the app
  /// keeps the same shape and only its colour reacts.
  static OutlineInputBorder _fieldBorder(Color color, {double width = 1}) =>
      OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: color, width: width),
      );

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
      // Fields are the app's most-touched surface, so they get the glass
      // language the cards already use — a translucent fill and a hairline
      // border — instead of Material's bare grey outline, which read as
      // unstyled against everything around it. Radius 14 matches
      // GradientButton so a field and the button under it agree.
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        // Resting vs focused fill: the lift is what tells you where the
        // caret is, without needing a colour to shout it.
        fillColor: glass,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
        border: _fieldBorder(glassBorder),
        enabledBorder: _fieldBorder(glassBorder),
        // Mint at 1.6px: the only strong colour on the field, spent on the
        // one thing that matters — which field is live.
        focusedBorder: _fieldBorder(mint, width: 1.6),
        errorBorder: _fieldBorder(tint(danger, 0.5)),
        focusedErrorBorder: _fieldBorder(danger, width: 1.6),
        disabledBorder: _fieldBorder(tint(glassBorder, 0.5)),
        labelStyle: const TextStyle(color: textMuted, fontSize: 14.5),
        floatingLabelStyle: const TextStyle(
          color: mint,
          fontSize: 13,
          fontWeight: FontWeight.w600,
        ),
        // Hints sit below the label in weight — muted, dimmed further, so a
        // filled field always reads louder than its placeholder.
        hintStyle: TextStyle(color: tint(textMuted, 0.6), fontSize: 14),
        helperStyle: const TextStyle(color: textMuted, fontSize: 11.5),
        errorStyle: const TextStyle(color: danger, fontSize: 11.5),
        // The prefix icon tracks focus with the border, so the whole field
        // lights up as one thing rather than in pieces.
        prefixIconColor: WidgetStateColor.resolveWith(
          (states) => states.contains(WidgetState.focused) ? mint : textMuted,
        ),
        suffixIconColor: WidgetStateColor.resolveWith(
          (states) => states.contains(WidgetState.focused) ? mint : textMuted,
        ),
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
