import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';

/// The shared shell behind both auth screens: the brand's drifting aurora
/// orbs over [Aurora.base], with the form scrolling on top.
///
/// The orbs are the one flourish here — everything else on these screens is
/// deliberately plain, because a sign-in form's job is to be understood, not
/// admired. Motion is slow (16–20s) and heavily blurred, so it reads as
/// ambient light rather than something demanding attention, and it stops
/// entirely under `prefers-reduced-motion` (`disableAnimations`).
class AuthScaffold extends StatefulWidget {
  const AuthScaffold({super.key, required this.child, this.appBar});

  final Widget child;
  final PreferredSizeWidget? appBar;

  @override
  State<AuthScaffold> createState() => _AuthScaffoldState();
}

class _AuthScaffoldState extends State<AuthScaffold>
    with SingleTickerProviderStateMixin {
  late final AnimationController _drift = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 20),
  );

  @override
  void initState() {
    super.initState();
    _drift.repeat(reverse: true);
  }

  @override
  void dispose() {
    _drift.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final still = MediaQuery.of(context).disableAnimations;
    if (still && _drift.isAnimating) _drift.stop();

    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: widget.appBar,
      // The orbs must not jump when the keyboard opens.
      resizeToAvoidBottomInset: false,
      body: Stack(
        children: [
          Positioned.fill(
            child: RepaintBoundary(
              child: AnimatedBuilder(
                animation: _drift,
                builder: (context, _) => CustomPaint(
                  painter: _AuroraPainter(t: still ? 0.5 : _drift.value),
                ),
              ),
            ),
          ),
          SafeArea(child: widget.child),
        ],
      ),
    );
  }
}

/// Centres a form in the space actually visible, and scrolls only when it
/// doesn't fit — so a short form sits in the middle of the screen instead of
/// hanging off the top with dead space beneath it.
///
/// The keyboard is handled here rather than by the Scaffold: [AuthScaffold]
/// keeps `resizeToAvoidBottomInset: false` so the aurora doesn't lurch when
/// the keyboard opens, which means the layout must subtract the inset itself.
/// Otherwise the form stays centred *behind* the keyboard, and Flutter's
/// scroll-into-view does nothing because the viewport it sees is still full
/// height.
class AuthForm extends StatelessWidget {
  const AuthForm({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final keyboard = MediaQuery.of(context).viewInsets.bottom;
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        padding: EdgeInsets.only(bottom: keyboard),
        child: ConstrainedBox(
          constraints: BoxConstraints(
            minHeight: math.max(0, constraints.maxHeight - keyboard),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(22, 24, 22, 24),
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

/// Three blurred colour fields drifting on offset paths. Painted rather than
/// stacked as blurred widgets: a MaskFilter blur on a circle is one cheap draw
/// call, where BackdropFilter/ImageFiltered would composite a full-screen
/// layer every frame for the same look.
class _AuroraPainter extends CustomPainter {
  const _AuroraPainter({required this.t});

  /// 0..1 drift phase.
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    void orb(Color color, Offset base, double radius, Offset travel) {
      final center = base + travel * t;
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..color = color
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 70),
      );
    }

    // Teal from the top-left, purple from the right, a fainter mint below —
    // the same three-colour arrangement as the marketing site's hero. The
    // centres sit partly off-canvas so only the soft shoulder of each field
    // shows, which is what keeps them reading as light rather than as shapes.
    orb(
      Aurora.teal.withValues(alpha: 0.38),
      Offset(size.width * 0.10, size.height * 0.06),
      size.width * 0.52,
      const Offset(26, 34),
    );
    orb(
      Aurora.purple.withValues(alpha: 0.34),
      Offset(size.width * 1.02, size.height * 0.26),
      size.width * 0.50,
      const Offset(-34, 30),
    );
    orb(
      Aurora.mint.withValues(alpha: 0.16),
      Offset(size.width * 0.22, size.height * 0.98),
      size.width * 0.55,
      const Offset(38, -30),
    );
  }

  @override
  bool shouldRepaint(_AuroraPainter old) => old.t != t;
}
