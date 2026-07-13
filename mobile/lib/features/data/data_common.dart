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
