import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';

/// The FarryOn wordmark + orb glyph, used as the crown of both auth screens.
class AuthBrand extends StatelessWidget {
  const AuthBrand({super.key});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Container(
          width: 30,
          height: 30,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: const RadialGradient(colors: [Aurora.mint, Aurora.teal]),
            boxShadow: [
              BoxShadow(
                color: Aurora.teal.withValues(alpha: 0.5),
                blurRadius: 18,
                spreadRadius: 1,
              ),
            ],
          ),
        ),
        const SizedBox(width: 10),
        const Text.rich(
          TextSpan(
            children: [
              TextSpan(text: 'Farry'),
              TextSpan(text: 'On', style: TextStyle(color: Aurora.mint)),
            ],
          ),
          style: TextStyle(
            color: Aurora.textPrimary,
            fontSize: 22,
            fontWeight: FontWeight.w700,
            letterSpacing: -0.3,
          ),
        ),
      ],
    );
  }
}

/// A tinted, icon-led message strip. The app's established way of reporting a
/// form-level outcome (see the email settings' connection test) — inline and
/// dismissible by acting, never a modal that has to be batted away.
class AuthBanner extends StatelessWidget {
  const AuthBanner.error(this.message, {super.key}) : _color = Aurora.danger;
  const AuthBanner.success(this.message, {super.key}) : _color = Aurora.mint;

  final String message;
  final Color _color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: _color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: _color.withValues(alpha: 0.3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            _color == Aurora.danger
                ? Icons.error_outline_rounded
                : Icons.check_circle_rounded,
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

/// A hairline rule with a word in the middle ("or").
class AuthDivider extends StatelessWidget {
  const AuthDivider(this.label, {super.key});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Expanded(child: Divider(color: Aurora.glassBorder, height: 1)),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Text(
            label,
            style: const TextStyle(color: Aurora.textMuted, fontSize: 11.5),
          ),
        ),
        const Expanded(child: Divider(color: Aurora.glassBorder, height: 1)),
      ],
    );
  }
}

/// "Continue with Google" — the neutral-surface variant from Google's branding
/// rules, which is what they require for a dark UI: their exact four-colour "G"
/// on a plain surface, unmodified, with the mandated wording. Deliberately NOT
/// gradient-styled: the mark and label may not be restyled to match a theme.
class GoogleButton extends StatelessWidget {
  const GoogleButton({super.key, required this.onPressed, this.busy = false});

  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: onPressed == null ? 0.5 : 1,
      child: Material(
        color: Aurora.glassStrong,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onPressed,
          child: Ink(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Aurora.glassBorder),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 13),
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
                  const SizedBox(width: 10),
                  // Flexible: Google's wording is fixed and can't be shortened,
                  // so at a large font scale let it ellipsize rather than
                  // overflow the button.
                  Flexible(
                    child: Text(
                      busy ? 'Signing in…' : 'Continue with Google',
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: Aurora.textPrimary,
                        fontSize: 14.5,
                        fontWeight: FontWeight.w600,
                      ),
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

/// Google's "G" drawn to their published geometry, so no asset ships with the
/// app and the mark stays crisp at any size. Fills whatever box it's given.
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
    final radius = (s - stroke) / 2;
    final rect = Rect.fromCircle(center: center, radius: radius);

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

    // One continuous ring — each arc starts exactly where the last ended. The
    // ONLY opening is on the right (-8°..20°), which is where the bar exits;
    // any other gap reads as a broken logo rather than a G.
    sweep(_red, -150, 90); // 10 o'clock over the top to 1 o'clock
    sweep(_blue, -60, 52); // 1 o'clock down to the bar
    // (gap -8°..20°: the bar's mouth)
    sweep(_green, 20, 100); // below the bar round to 7 o'clock
    sweep(_yellow, 120, 90); // 7 o'clock back up to 10 o'clock — meets red

    // The bar, in blue like the arc it continues from: it fills the mouth and
    // runs to the right edge, sitting just below the centre line.
    canvas.drawRect(
      Rect.fromLTRB(s * 0.52, s * 0.42, s * 0.97, s * 0.42 + stroke * 0.95),
      Paint()..color = _blue,
    );
  }

  @override
  bool shouldRepaint(_GoogleGPainter oldDelegate) => false;
}
