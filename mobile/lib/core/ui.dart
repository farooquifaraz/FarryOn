import 'package:flutter/material.dart';

import 'theme.dart';

/// Reusable "Midnight Aurora" building blocks for the redesigned surfaces.
///
/// These are purely presentational — they never touch state or config. The
/// signature move is [GradientIcon]: a rounded Material icon whose glyph is
/// filled with one of [Aurora]'s category gradients (via a [ShaderMask]), so
/// every icon in the app reads as colourful without changing what it does.

/// A rounded icon whose glyph is painted with [gradient] instead of a flat
/// colour. Falls back cleanly to a solid tint if a gradient isn't supplied.
class GradientIcon extends StatelessWidget {
  const GradientIcon(
    this.icon, {
    super.key,
    this.gradient = Aurora.gradTeal,
    this.size = 24,
  });

  final IconData icon;
  final Gradient gradient;
  final double size;

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (bounds) =>
          gradient.createShader(Offset.zero & bounds.size),
      // The child colour is irrelevant (srcIn replaces it with the shader),
      // but must be opaque white so the whole glyph picks up the gradient.
      child: Icon(icon, size: size, color: Colors.white),
    );
  }
}

/// A small rounded "glass" tile holding a [GradientIcon] — the leading element
/// of every settings row and grid cell.
class GradientIconTile extends StatelessWidget {
  const GradientIconTile(
    this.icon, {
    super.key,
    this.gradient = Aurora.gradTeal,
    this.tileSize = 40,
    this.iconSize = 22,
    this.radius = 11,
  });

  final IconData icon;
  final Gradient gradient;
  final double tileSize;
  final double iconSize;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: tileSize,
      height: tileSize,
      decoration: BoxDecoration(
        color: Aurora.glass,
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(color: Aurora.glassBorder),
      ),
      alignment: Alignment.center,
      child: GradientIcon(icon, gradient: gradient, size: iconSize),
    );
  }
}

/// The full-width primary action button (Save, reconnect, …) with the teal
/// [Aurora.primaryGradient] fill and dark-teal ink.
class GradientButton extends StatelessWidget {
  const GradientButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.gradient = Aurora.primaryGradient,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final Gradient gradient;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: onPressed == null ? 0.5 : 1,
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: gradient,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: onPressed,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 14),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (icon != null) ...[
                    Icon(icon, size: 20, color: Aurora.tealInk),
                    const SizedBox(width: 8),
                  ],
                  Text(
                    label,
                    style: const TextStyle(
                      color: Aurora.tealInk,
                      fontSize: 15,
                      fontWeight: FontWeight.w700,
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

/// An uppercase mint section label (matches the existing settings `_label`).
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
        child: Text(
          text.toUpperCase(),
          style: const TextStyle(
            color: Aurora.mint,
            fontSize: 12,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.6,
          ),
        ),
      );
}

/// A tappable settings row: gradient icon tile · title + subtitle · trailing.
/// The trailing defaults to a chevron; pass any widget (e.g. a Switch) instead.
class SettingsRow extends StatelessWidget {
  const SettingsRow({
    super.key,
    required this.icon,
    required this.gradient,
    required this.title,
    this.subtitle,
    this.subtitleColor,
    this.onTap,
    this.trailing,
    this.showDivider = true,
  });

  final IconData icon;
  final Gradient gradient;
  final String title;
  final String? subtitle;
  final Color? subtitleColor;
  final VoidCallback? onTap;
  final Widget? trailing;
  final bool showDivider;

  @override
  Widget build(BuildContext context) {
    final row = Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      child: Row(
        children: [
          GradientIconTile(icon, gradient: gradient, tileSize: 40, iconSize: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: const TextStyle(
                        color: Aurora.textPrimary, fontSize: 14)),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(subtitle!,
                      style: TextStyle(
                          color: subtitleColor ?? Aurora.textMuted,
                          fontSize: 12)),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          trailing ??
              const Icon(Icons.chevron_right_rounded, color: Aurora.textMuted),
        ],
      ),
    );

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (onTap != null)
          InkWell(onTap: onTap, child: row)
        else
          row,
        if (showDivider)
          const Divider(height: 1, color: Aurora.glassBorder, indent: 64),
      ],
    );
  }
}

/// A rounded "glass" card that wraps a group of [SettingsRow]s.
class SettingsGroup extends StatelessWidget {
  const SettingsGroup({super.key, required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(
          color: Aurora.glass,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Aurora.glassBorder),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(mainAxisSize: MainAxisSize.min, children: children),
      );
}
