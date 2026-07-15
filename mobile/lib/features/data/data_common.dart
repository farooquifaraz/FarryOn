import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';

/// Shared pieces for the Notes / Reminders / Conversations screens: date
/// helpers, task grouping, and the empty / error / card states — all in the
/// redesigned Midnight Aurora + gradient style.

/// "21 Jun, 17:43"-style label for a saved item.
String dataWhen(DateTime d) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  final h = d.hour.toString().padLeft(2, '0');
  final m = d.minute.toString().padLeft(2, '0');
  return '${d.day} ${months[d.month - 1]}, $h:$m';
}

/// Short date for an ISO timestamp (e.g. note created-at), or null if absent.
String? dataDate(String? iso) {
  if (iso == null || iso.isEmpty) return null;
  final d = DateTime.tryParse(iso)?.toLocal();
  return d == null ? null : dataWhen(d);
}

/// A short, friendly due label for a task's ISO date.
String? dataDueLabel(String? iso) {
  if (iso == null || iso.isEmpty) return null;
  final d = DateTime.tryParse(iso)?.toLocal();
  if (d == null) return iso;
  final h = d.hour.toString().padLeft(2, '0');
  final m = d.minute.toString().padLeft(2, '0');
  return 'due ${d.day}/${d.month} $h:$m';
}

/// Bucket tasks by due date for grouped display (Overdue → No date).
List<({String label, List<TaskItem> items})> groupTasksByDate(
    List<TaskItem> tasks) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final tomorrow = today.add(const Duration(days: 1));
  final weekEnd = today.add(const Duration(days: 7));
  const order = [
    'Overdue', 'Today', 'Tomorrow', 'This week', 'Later', 'No date',
  ];
  final buckets = {for (final k in order) k: <TaskItem>[]};

  for (final t in tasks) {
    final due = (t.dueDate != null && t.dueDate!.isNotEmpty)
        ? DateTime.tryParse(t.dueDate!)?.toLocal()
        : null;
    if (due == null) {
      buckets['No date']!.add(t);
      continue;
    }
    final day = DateTime(due.year, due.month, due.day);
    if (!t.done && due.isBefore(now)) {
      buckets['Overdue']!.add(t);
    } else if (day == today) {
      buckets['Today']!.add(t);
    } else if (day == tomorrow) {
      buckets['Tomorrow']!.add(t);
    } else if (day.isAfter(today) && day.isBefore(weekEnd)) {
      buckets['This week']!.add(t);
    } else if (day.isBefore(today)) {
      buckets['Overdue']!.add(t);
    } else {
      buckets['Later']!.add(t);
    }
  }
  return [
    for (final k in order)
      if (buckets[k]!.isNotEmpty) (label: k, items: buckets[k]!),
  ];
}

/// A "glass" card wrapper used by the list rows.
class DataCard extends StatelessWidget {
  const DataCard({super.key, required this.child, this.padding});
  final Widget child;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding ?? const EdgeInsets.fromLTRB(14, 8, 8, 8),
        decoration: BoxDecoration(
          color: Aurora.glass,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Aurora.glassBorder),
        ),
        child: child,
      );
}

/// A friendly empty state with a large gradient icon.
class DataEmptyState extends StatelessWidget {
  const DataEmptyState({
    super.key,
    required this.icon,
    required this.gradient,
    required this.label,
  });

  final IconData icon;
  final Gradient gradient;
  final String label;

  @override
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            GradientIcon(icon, gradient: gradient, size: 56),
            const SizedBox(height: 14),
            Text(label,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Aurora.textMuted, height: 1.4)),
          ],
        ),
      );
}

/// A themed floating toast — used to report a failed mutation without a
/// jarring dialog. [error] tints it danger-red.
void dataSnack(BuildContext context, String message, {bool error = false}) {
  if (!context.mounted) return;
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        behavior: SnackBarBehavior.floating,
        backgroundColor: error ? Aurora.danger : Aurora.surfaceHigh,
        duration: const Duration(milliseconds: 2200),
      ),
    );
}

/// Ask before an irreversible action. Resolves true only on an explicit
/// confirm. Themed to match the Midnight Aurora surfaces.
Future<bool> confirmAction(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Delete',
  bool danger = true,
}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      backgroundColor: Aurora.surfaceHigh,
      shape:
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      title: Text(title, style: const TextStyle(color: Aurora.textPrimary)),
      content: Text(message,
          style: const TextStyle(color: Aurora.textMuted, height: 1.4)),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx, false),
          child:
              const Text('Cancel', style: TextStyle(color: Aurora.textMuted)),
        ),
        TextButton(
          onPressed: () => Navigator.pop(ctx, true),
          child: Text(
            confirmLabel,
            style: TextStyle(
              color: danger ? Aurora.danger : Aurora.mint,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
      ],
    ),
  );
  return ok ?? false;
}

/// A small uppercase count line above a list (e.g. "12 NOTES"), giving each
/// screen a professional summary header instead of a bare list.
class DataCountHeader extends StatelessWidget {
  const DataCountHeader({super.key, required this.text, this.accent});
  final String text;
  final Color? accent;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 2, 4, 12),
        child: Text(
          text.toUpperCase(),
          style: TextStyle(
            color: accent ?? Aurora.mint,
            fontSize: 12,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.5,
          ),
        ),
      );
}

/// Wraps a list row so it can be swiped away (end-to-start) onto a red
/// gradient, with a confirm step. Cleaner than an always-visible trash icon.
class SwipeToDelete extends StatelessWidget {
  const SwipeToDelete({
    super.key,
    required this.dismissKey,
    required this.child,
    required this.confirm,
    required this.onDismissed,
    this.radius = 16,
  });

  final Key dismissKey;
  final Widget child;

  /// Return true to proceed with removal (e.g. after a confirm dialog).
  final Future<bool> Function() confirm;
  final VoidCallback onDismissed;
  final double radius;

  @override
  Widget build(BuildContext context) => Dismissible(
        key: dismissKey,
        direction: DismissDirection.endToStart,
        confirmDismiss: (_) => confirm(),
        onDismissed: (_) => onDismissed(),
        background: Container(
          alignment: Alignment.centerRight,
          padding: const EdgeInsets.only(right: 22),
          decoration: BoxDecoration(
            gradient: Aurora.gradCoral,
            borderRadius: BorderRadius.circular(radius),
          ),
          child: const Icon(Icons.delete_rounded,
              color: Aurora.tealInk, size: 22),
        ),
        child: child,
      );
}

/// A backend-error state with a retry button.
class DataErrorState extends StatelessWidget {
  const DataErrorState({super.key, required this.onRetry});
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const GradientIcon(Icons.cloud_off_rounded,
                gradient: Aurora.gradAmber, size: 44),
            const SizedBox(height: 12),
            const Text("Couldn't load — check the backend.",
                style: TextStyle(color: Aurora.textMuted)),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      );
}
