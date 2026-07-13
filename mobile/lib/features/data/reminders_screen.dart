import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';
import 'data_common.dart';

/// Time-based reminders & tasks, grouped Overdue → Later. Toggle done, delete;
/// add is voice-only ("remind me …").
class RemindersScreen extends ConsumerStatefulWidget {
  const RemindersScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const RemindersScreen()),
      );

  @override
  ConsumerState<RemindersScreen> createState() => _RemindersScreenState();
}

class _RemindersScreenState extends ConsumerState<RemindersScreen> {
  late final DataApi _api = ref.read(dataApiProvider);
  late Future<List<TaskItem>> _future = _api.tasks();

  void _reload() => setState(() => _future = _api.tasks());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        title: const Row(
          children: [
            GradientIcon(Icons.alarm_rounded,
                gradient: Aurora.gradAmber, size: 22),
            SizedBox(width: 10),
            Text('Reminders'),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: () async => _reload(),
        child: FutureBuilder<List<TaskItem>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _scrollable(DataErrorState(onRetry: _reload));
            }
            final tasks = snap.data ?? const [];
            if (tasks.isEmpty) {
              return _scrollable(const DataEmptyState(
                icon: Icons.alarm_rounded,
                gradient: Aurora.gradAmber,
                label: 'No reminders yet.\nSay "remind me…" to add one.',
              ));
            }
            final children = <Widget>[];
            for (final g in groupTasksByDate(tasks)) {
              children.add(_GroupHeader(label: g.label, count: g.items.length));
              final accent = _groupColor(g.label);
              children.addAll(g.items.map((t) => _tile(t, accent)));
            }
            return ListView(
                padding: const EdgeInsets.all(14), children: children);
          },
        ),
      ),
    );
  }

  Widget _tile(TaskItem t, Color accent) {
    final due = dataDueLabel(t.dueDate);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 12, 6, 12),
        decoration: BoxDecoration(
          color: Aurora.surfaceHigh,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Aurora.glassBorder),
        ),
        child: Row(
          children: [
            _CheckCircle(
              done: t.done,
              accent: accent,
              onTap: () async {
                await _api.setTaskDone(t.id, !t.done);
                _reload();
              },
            ),
            const SizedBox(width: 13),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    t.title,
                    style: TextStyle(
                      color: t.done ? Aurora.textMuted : Aurora.textPrimary,
                      fontSize: 15,
                      decoration: t.done ? TextDecoration.lineThrough : null,
                    ),
                  ),
                  if (due != null) ...[
                    const SizedBox(height: 7),
                    _DuePill(label: due, accent: t.done ? Aurora.textMuted : accent),
                  ],
                ],
              ),
            ),
            InkWell(
              borderRadius: BorderRadius.circular(20),
              onTap: () async {
                await _api.deleteTask(t.id);
                _reload();
              },
              child: const Padding(
                padding: EdgeInsets.all(6),
                child: Icon(Icons.delete_outline_rounded,
                    size: 20, color: Aurora.textMuted),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Accent colour per due-date bucket, so the eye can triage at a glance.
  Color _groupColor(String label) => switch (label) {
        'Overdue' => Aurora.danger,
        'Today' => Aurora.teal,
        'Tomorrow' => Aurora.purple,
        'This week' => const Color(0xFF378ADD),
        'Later' => Aurora.mint,
        _ => Aurora.textMuted,
      };

  Widget _scrollable(Widget child) => ListView(
        padding: const EdgeInsets.only(top: 120),
        children: [child],
      );
}

class _GroupHeader extends StatelessWidget {
  const _GroupHeader({required this.label, required this.count});
  final String label;
  final int count;

  @override
  Widget build(BuildContext context) {
    final danger = label == 'Overdue';
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 6, 4, 10),
      child: Row(
        children: [
          Text(
            label.toUpperCase(),
            style: TextStyle(
              color: danger ? Aurora.danger : Aurora.mint,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.4,
            ),
          ),
          const SizedBox(width: 6),
          Text('$count',
              style: const TextStyle(color: Aurora.textMuted, fontSize: 12)),
        ],
      ),
    );
  }
}

/// A tappable round check: gradient-green filled with a tick when done, an
/// accent-coloured ring when still open.
class _CheckCircle extends StatelessWidget {
  const _CheckCircle({
    required this.done,
    required this.accent,
    required this.onTap,
  });

  final bool done;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Container(
        width: 26,
        height: 26,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: done ? Aurora.gradGreen : null,
          border: done ? null : Border.all(color: accent, width: 2),
        ),
        child: done
            ? const Icon(Icons.check_rounded, size: 16, color: Colors.white)
            : null,
      ),
    );
  }
}

/// A tinted "due …" pill, coloured by the task's urgency bucket.
class _DuePill extends StatelessWidget {
  const _DuePill({required this.label, required this.accent});

  final String label;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: Aurora.tint(accent, 0.16),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.alarm_rounded, size: 13, color: accent),
          const SizedBox(width: 5),
          Text(label,
              style: TextStyle(
                  color: accent, fontSize: 11, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
