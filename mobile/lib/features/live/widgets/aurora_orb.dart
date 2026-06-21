import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../protocol/protocol.dart';

/// The signature "Aurora" voice orb — concentric translucent rings that pulse
/// gently and shift colour with the assistant's [LiveState]:
///
///   * idle      → faint lavender, barely breathing
///   * listening → mint, attentive
///   * thinking  → purple
///   * speaking  → teal, the strongest pulse
///
/// It is purely decorative (wrapped in [IgnorePointer]) and sits over the camera
/// hero, giving the screen a calm, alive focal point without blocking the view.
class AuroraOrb extends StatefulWidget {
  const AuroraOrb({super.key, required this.state, this.size = 170});

  final LiveState state;
  final double size;

  @override
  State<AuroraOrb> createState() => _AuroraOrbState();
}

class _AuroraOrbState extends State<AuroraOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Color get _color => switch (widget.state) {
        LiveState.listening => Aurora.mint,
        LiveState.thinking => Aurora.purple,
        LiveState.speaking => Aurora.teal,
        LiveState.idle => Aurora.purpleSoft,
      };

  // Idle orbits quietly; active states glow at full strength.
  double get _intensity => widget.state == LiveState.idle ? 0.45 : 1.0;

  @override
  Widget build(BuildContext context) {
    final color = _color;
    return IgnorePointer(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final t = Curves.easeInOut.transform(_controller.value);
          final pulse = 0.9 + 0.12 * t * _intensity;
          return SizedBox(
            width: widget.size,
            height: widget.size,
            child: Center(
              child: Stack(
                alignment: Alignment.center,
                children: [
                  _ring(widget.size * pulse,
                      color.withValues(alpha: 0.10 * _intensity)),
                  _ring(widget.size * 0.68 * pulse,
                      color.withValues(alpha: 0.20 * _intensity)),
                  _ring(widget.size * 0.38,
                      color.withValues(alpha: 0.55)),
                  _ring(widget.size * 0.18, color),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _ring(double size, Color color) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(shape: BoxShape.circle, color: color),
      );
}
