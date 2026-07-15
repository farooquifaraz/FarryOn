import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';

/// The shared backdrop behind every auth screen: a teal wash climbing from
/// near-black at the top to a neon glow at the bottom, with two soft radial
/// accents lifting it off flat.
///
/// Splash, sign-in and sign-up all use this, so moving between them never
/// changes the ground under the user's feet.
class AuthScaffold extends StatelessWidget {
  const AuthScaffold({super.key, required this.child, this.appBar});

  final Widget child;
  final PreferredSizeWidget? appBar;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);

    return Scaffold(
      // The gradient's dark end, so any pixel the gradient doesn't cover
      // (rotation, overscroll) still matches its top.
      backgroundColor: const Color(0xFF06140F),
      appBar: appBar,
      // The backdrop must not be squeezed when the keyboard opens; the form
      // subtracts the inset itself (see AuthForm).
      resizeToAvoidBottomInset: false,
      body: DecoratedBox(
        decoration: const BoxDecoration(gradient: Aurora.authBackdrop),
        child: Stack(
          children: [
            // Two glow accents: one behind the logo, one hugging the bottom.
            // Painted, not stacked as blurred widgets — a radial gradient is
            // one cheap draw where an ImageFiltered layer would composite the
            // whole screen every frame for the same look.
            Positioned.fill(
              child: IgnorePointer(
                child: CustomPaint(painter: _GlowPainter(size: size)),
              ),
            ),
            SafeArea(child: child),
          ],
        ),
      ),
    );
  }
}

class _GlowPainter extends CustomPainter {
  const _GlowPainter({required this.size});
  final Size size;

  @override
  void paint(Canvas canvas, Size canvasSize) {
    void glow(Offset center, double radius, double opacity, double stop) {
      final rect = Rect.fromCircle(center: center, radius: radius);
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..shader = RadialGradient(
            colors: [
              Aurora.neon.withValues(alpha: opacity),
              Aurora.neon.withValues(alpha: 0),
            ],
            stops: [0, stop],
          ).createShader(rect),
      );
    }

    // Upper: sits behind the logo, ~34% down.
    glow(
      Offset(canvasSize.width / 2, canvasSize.height * 0.34),
      canvasSize.width * 0.6,
      0.20,
      0.68,
    );
    // Lower: hugs the bottom edge, feeding the gradient's neon end.
    glow(
      Offset(canvasSize.width / 2, canvasSize.height * 1.02),
      canvasSize.width * 0.75,
      0.16,
      0.70,
    );
  }

  @override
  bool shouldRepaint(_GlowPainter old) => old.size != size;
}

/// Centres a form in the space actually visible, and scrolls only when it
/// doesn't fit — so a short form sits in the middle of the screen instead of
/// hanging off the top with dead space beneath it.
///
/// The keyboard is handled here rather than by the Scaffold: [AuthScaffold]
/// keeps `resizeToAvoidBottomInset: false` so the backdrop doesn't get
/// squeezed when the keyboard opens, which means the layout must subtract the
/// inset itself. Otherwise the form stays centred *behind* the keyboard, and
/// Flutter's scroll-into-view does nothing because the viewport it sees is
/// still full height.
class AuthForm extends StatelessWidget {
  const AuthForm({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final keyboard = MediaQuery.viewInsetsOf(context).bottom;
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        padding: EdgeInsets.only(bottom: keyboard),
        child: ConstrainedBox(
          constraints: BoxConstraints(
            minHeight: math.max(0, constraints.maxHeight - keyboard),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(26, 18, 26, 24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: children,
            ),
          ),
        ),
      ),
    );
  }
}
