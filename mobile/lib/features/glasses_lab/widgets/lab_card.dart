import 'package:flutter/material.dart';

import '../../../core/theme.dart';

/// Shared shell for every Glasses Lab card: icon + title + status chip on the
/// header row, then the card's own controls. Keeps all six functionality
/// cards visually identical ("Midnight Aurora" raised card).
class LabCard extends StatelessWidget {
  const LabCard({
    super.key,
    required this.icon,
    required this.title,
    required this.child,
    this.status,
    this.statusColor,
  });

  final IconData icon;
  final String title;

  /// Short state label shown as a tinted pill (e.g. "connected", "idle").
  final String? status;
  final Color? statusColor;

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Aurora.surfaceHigh,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 20, color: Aurora.mint),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                    color: Aurora.textPrimary,
                  ),
                ),
              ),
              if (status != null) LabStatusPill(status!, color: statusColor),
            ],
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}

/// Small tinted status pill used in card headers and rows.
class LabStatusPill extends StatelessWidget {
  const LabStatusPill(this.label, {super.key, this.color});

  final String label;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final c = color ?? Aurora.textMuted;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: Aurora.tint(c),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: c),
      ),
    );
  }
}

/// A `label: value` line used across the info-style cards.
class LabKv extends StatelessWidget {
  const LabKv(this.label, this.value, {super.key});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 110,
            child: Text(label,
                style: const TextStyle(fontSize: 12.5, color: Aurora.textMuted)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(
                    fontSize: 12.5, color: Aurora.textPrimary)),
          ),
        ],
      ),
    );
  }
}
