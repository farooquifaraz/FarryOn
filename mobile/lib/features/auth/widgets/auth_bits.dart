import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';

/// The brand mark with a neon glow behind it — the crown of every auth
/// screen.
class AuthLogo extends StatelessWidget {
  const AuthLogo({super.key, this.size = 118});

  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size * 1.25,
      height: size * 1.25,
      child: Stack(
        alignment: Alignment.center,
        children: [
          DecoratedBox(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [
                  Aurora.neon.withValues(alpha: 0.22),
                  Aurora.neon.withValues(alpha: 0),
                ],
                stops: const [0, 0.65],
              ),
            ),
            child: SizedBox(width: size * 1.25, height: size * 1.25),
          ),
          Container(
            width: size,
            height: size,
            decoration: BoxDecoration(
              boxShadow: [
                BoxShadow(
                  color: Aurora.neon.withValues(alpha: 0.4),
                  blurRadius: 24,
                ),
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.25),
                  blurRadius: 16,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Image.asset(
              'assets/logo/embossed-mark-1024.png',
              fit: BoxFit.contain,
              // A missing asset must not leave a blank screen where the brand
              // should be — fall back to a glyph in the same colour.
              errorBuilder: (_, __, ___) => Icon(
                Icons.auto_awesome_rounded,
                size: size * 0.7,
                color: Aurora.neon,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A pill-shaped input with its label above it. Focus lights the border neon
/// and lays a faint halo around it, so the live field is unmistakable against
/// a backdrop that is itself teal.
///
/// Deliberately NOT the app-wide `inputDecorationTheme`: the auth screens sit
/// on the bright gradient and need their own darker fill, while every other
/// form in the app sits on near-black and uses the glass fields.
class AuthField extends StatefulWidget {
  const AuthField({
    super.key,
    this.label,
    required this.hint,
    this.controller,
    this.obscureText = false,
    this.keyboardType,
    this.textInputAction,
    this.textCapitalization = TextCapitalization.none,
    this.autofillHints,
    this.autocorrect = true,
    this.enableSuggestions = true,
    this.readOnly = false,
    this.autofocus = false,
    this.focusNode,
    this.onChanged,
    this.onSubmitted,
    this.suffix,
    this.textAlign = TextAlign.start,
    this.style,
  });

  final String? label;
  final String hint;
  final TextEditingController? controller;
  final bool obscureText;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final TextCapitalization textCapitalization;
  final List<String>? autofillHints;
  final bool autocorrect;
  final bool enableSuggestions;
  final bool readOnly;
  final bool autofocus;
  final FocusNode? focusNode;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final Widget? suffix;
  final TextAlign textAlign;
  final TextStyle? style;

  @override
  State<AuthField> createState() => _AuthFieldState();
}

class _AuthFieldState extends State<AuthField> {
  FocusNode? _owned;
  FocusNode get _node => widget.focusNode ?? (_owned ??= FocusNode());
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    _node.addListener(_onFocus);
  }

  void _onFocus() {
    if (mounted && _focused != _node.hasFocus) {
      setState(() => _focused = _node.hasFocus);
    }
  }

  @override
  void dispose() {
    _node.removeListener(_onFocus);
    // Only dispose a node we created — a caller's node is the caller's to own.
    _owned?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (widget.label != null) ...[
          Text(
            widget.label!,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w500,
              color: Aurora.authTextDim,
            ),
          ),
          const SizedBox(height: 6),
        ],
        AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          decoration: BoxDecoration(
            color: Aurora.authFieldFill,
            borderRadius: BorderRadius.circular(26),
            border: Border.all(
              color: _focused
                  ? Aurora.neon.withValues(alpha: 0.55)
                  : Aurora.authFieldBorder,
              width: 1.3,
            ),
            boxShadow: _focused
                ? [
                    BoxShadow(
                      color: Aurora.neon.withValues(alpha: 0.10),
                      spreadRadius: 3,
                    ),
                  ]
                : null,
          ),
          child: TextField(
            controller: widget.controller,
            focusNode: _node,
            obscureText: widget.obscureText,
            keyboardType: widget.keyboardType,
            textInputAction: widget.textInputAction,
            textCapitalization: widget.textCapitalization,
            autofillHints: widget.autofillHints,
            autocorrect: widget.autocorrect,
            enableSuggestions: widget.enableSuggestions,
            // readOnly, not disabled: disabling a focused field drops the
            // keyboard, so a failed submit would fight the user's retry.
            readOnly: widget.readOnly,
            autofocus: widget.autofocus,
            onChanged: widget.onChanged,
            onSubmitted: widget.onSubmitted,
            textAlign: widget.textAlign,
            cursorColor: Aurora.neon,
            style: widget.style ??
                const TextStyle(
                  fontSize: 14.5,
                  color: Colors.white,
                ),
            decoration: InputDecoration(
              hintText: widget.hint,
              hintStyle: const TextStyle(
                fontSize: 14.5,
                color: Aurora.authTextFaint,
              ),
              suffixIcon: widget.suffix,
              // The pill is drawn by the container above; the field itself
              // must add no border of its own.
              border: InputBorder.none,
              enabledBorder: InputBorder.none,
              focusedBorder: InputBorder.none,
              filled: false,
              isDense: true,
              contentPadding:
                  const EdgeInsets.symmetric(vertical: 15, horizontal: 18),
            ),
          ),
        ),
      ],
    );
  }
}

/// The primary action. Teal-to-blue so it lifts off a backdrop that is teal
/// all the way down, and it dips slightly under the thumb.
class AuthCtaButton extends StatefulWidget {
  const AuthCtaButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.loading = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool loading;

  @override
  State<AuthCtaButton> createState() => _AuthCtaButtonState();
}

class _AuthCtaButtonState extends State<AuthCtaButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final disabled = widget.onPressed == null || widget.loading;

    return GestureDetector(
      onTapDown: disabled ? null : (_) => setState(() => _pressed = true),
      onTapUp: disabled ? null : (_) => setState(() => _pressed = false),
      onTapCancel: disabled ? null : () => setState(() => _pressed = false),
      onTap: disabled ? null : widget.onPressed,
      child: AnimatedScale(
        scale: _pressed ? 0.97 : 1,
        duration: const Duration(milliseconds: 120),
        child: Container(
          height: Aurora.authButtonHeight,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
            gradient: disabled
                ? LinearGradient(colors: [
                    const Color(0xFF00D9A6).withValues(alpha: 0.4),
                    const Color(0xFF12A6E0).withValues(alpha: 0.4),
                  ])
                : Aurora.authCta,
            // A glowing button you can't press is a lie — drop it when
            // disabled.
            boxShadow: disabled
                ? null
                : [
                    BoxShadow(
                      color: const Color(0xFF00D9A6).withValues(alpha: 0.32),
                      blurRadius: 28,
                      offset: const Offset(0, 10),
                    ),
                  ],
          ),
          alignment: Alignment.center,
          child: widget.loading
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.4,
                    valueColor: AlwaysStoppedAnimation(Aurora.authInk),
                  ),
                )
              : Text(widget.label, style: _buttonLabel(Aurora.authInk)),
        ),
      ),
    );
  }
}

/// The secondary action on the splash — solid white, so "Create Account"
/// reads as an equal choice rather than an afterthought.
class AuthWhiteButton extends StatelessWidget {
  const AuthWhiteButton({super.key, required this.label, required this.onPressed});

  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
      elevation: 0,
      child: InkWell(
        onTap: onPressed,
        borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
        child: SizedBox(
          height: Aurora.authButtonHeight,
          child: Center(child: Text(label, style: _buttonLabel(Aurora.authInk))),
        ),
      ),
    );
  }
}

/// "Continue with Google" — the neutral-surface variant Google's branding
/// rules require on a dark UI: their exact four-colour mark, unmodified,
/// with their mandated wording. Same 52px/30r as every other auth button.
class GoogleButton extends StatelessWidget {
  const GoogleButton({super.key, required this.onPressed, this.busy = false});

  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: onPressed == null ? 0.5 : 1,
      // The height goes OUTSIDE the border, not inside it: a 1.2px border on a
      // 52px child makes a 54.4px button, which is exactly the mismatch the
      // shared spec exists to prevent (and which the test caught).
      child: SizedBox(
        height: Aurora.authButtonHeight,
        child: Material(
          color: Aurora.authMutedFill,
          borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
          child: InkWell(
            onTap: onPressed,
            borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
            child: Ink(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(Aurora.authButtonRadius),
                border: Border.all(color: Aurora.authMutedBorder, width: 1.2),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 18,
                    height: 18,
                    child: busy
                        ? const CircularProgressIndicator(strokeWidth: 2)
                        : const GoogleGlyph(),
                  ),
                  const SizedBox(width: 9),
                  // Google's wording is fixed and can't be shortened, so at a
                  // large font scale let it ellipsize rather than overflow.
                  Flexible(
                    child: Text(
                      busy ? 'Signing in…' : 'Continue with Google',
                      overflow: TextOverflow.ellipsis,
                      style: _buttonLabel(Colors.white),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

TextStyle _buttonLabel(Color color) => TextStyle(
      fontSize: 15,
      fontWeight: FontWeight.w700,
      letterSpacing: 0.1,
      color: color,
    );

/// A hairline rule fading out from a word in the middle.
class AuthDivider extends StatelessWidget {
  const AuthDivider({super.key, this.label = 'OR'});
  final String label;

  @override
  Widget build(BuildContext context) {
    Widget rule(List<Color> colors) => Expanded(
          child: Container(
            height: 1,
            decoration: BoxDecoration(gradient: LinearGradient(colors: colors)),
          ),
        );

    final faint = Aurora.neon.withValues(alpha: 0.18);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Row(
        children: [
          rule([Colors.transparent, faint]),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.2,
                color: Aurora.authTextFaint,
              ),
            ),
          ),
          rule([faint, Colors.transparent]),
        ],
      ),
    );
  }
}

/// A tinted, icon-led message strip — the app's established way of reporting
/// a form-level outcome, inline rather than in a modal that has to be batted
/// away.
class AuthBanner extends StatelessWidget {
  const AuthBanner.error(this.message, {super.key}) : _color = const Color(0xFFFF8A80);
  const AuthBanner.success(this.message, {super.key}) : _color = Aurora.neon;

  final String message;
  final Color _color;

  @override
  Widget build(BuildContext context) {
    final error = _color != Aurora.neon;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: _color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: _color.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            error ? Icons.error_outline_rounded : Icons.check_circle_rounded,
            color: _color,
            size: 18,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: _color, fontSize: 12.5, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

/// Google's "G" drawn to their published geometry, so no asset ships and the
/// mark stays crisp at any size. Fills whatever box it's given.
class GoogleGlyph extends StatelessWidget {
  const GoogleGlyph({super.key});

  @override
  Widget build(BuildContext context) =>
      CustomPaint(painter: _GoogleGPainter(), size: Size.infinite);
}

class _GoogleGPainter extends CustomPainter {
  // Google's brand colours — must not be altered.
  static const _blue = Color(0xFF4285F4);
  static const _green = Color(0xFF34A853);
  static const _yellow = Color(0xFFFBBC05);
  static const _red = Color(0xFFEA4335);

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width;
    final center = Offset(s / 2, s / 2);
    final stroke = s * 0.22;
    final rect = Rect.fromCircle(center: center, radius: (s - stroke) / 2);

    final arc = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.butt;

    // Angles are Flutter's: 0° points right (3 o'clock), positive is clockwise.
    void sweep(Color c, double startDeg, double sweepDeg) {
      canvas.drawArc(
        rect,
        startDeg * math.pi / 180,
        sweepDeg * math.pi / 180,
        false,
        arc..color = c,
      );
    }

    // One continuous ring — each arc starts where the last ended. The ONLY
    // opening is on the right (-8°..20°), where the bar exits; any other gap
    // reads as a broken logo rather than a G.
    sweep(_red, -150, 90); // 10 o'clock over the top to 1 o'clock
    sweep(_blue, -60, 52); // 1 o'clock down to the bar
    sweep(_green, 20, 100); // below the bar round to 7 o'clock
    sweep(_yellow, 120, 90); // 7 o'clock back up to meet red

    // The bar, in blue like the arc it continues from.
    canvas.drawRect(
      Rect.fromLTRB(s * 0.52, s * 0.42, s * 0.97, s * 0.42 + stroke * 0.95),
      Paint()..color = _blue,
    );
  }

  @override
  bool shouldRepaint(_GoogleGPainter oldDelegate) => false;
}
