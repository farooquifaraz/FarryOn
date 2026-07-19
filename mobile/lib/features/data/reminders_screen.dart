import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/data_cache.dart';
import '../../core/outbox.dart';
import '../../core/outbox_sync.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/data_api.dart';
import '../../state/auth.dart';
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
  List<TaskItem>? _tasks;
  bool _loading = true;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final userId = ref.read(authProvider).userId;
    final cached = DataCache.tasks(userId);
    if (cached != null && _tasks == null) {
      setState(() {
        _tasks = cached;
        _loading = false;
      });
    }

    setState(() {
      _loading = _tasks == null;
      _error = false;
    });
    try {
      // The server is reachable, so anything queued offline can go now — and it
      // must go BEFORE the fetch, or the reply would hand back the rows we
      // just deleted and put them straight back on screen.
      await OutboxSync.drain(_api, userId);
      final t = await _api.tasks();
      if (!mounted) return;
      unawaited(DataCache.saveTasks(userId, t));
      setState(() {
        _tasks = t;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = _tasks == null; // only if we have nothing to show
        _loading = false;
      });
    }
  }

  int get _overdueCount {
    final now = DateTime.now();
    return (_tasks ?? const []).where((t) {
      if (t.done || t.dueDate == null || t.dueDate!.isEmpty) return false;
      final d = DateTime.tryParse(t.dueDate!)?.toLocal();
      return d != null && d.isBefore(now);
    }).length;
  }

  /// Optimistic toggle: the flip lands now, and is queued if we're offline.
  Future<void> _toggle(TaskItem t) async {
    setState(() => _tasks = [
          for (final x in _tasks ?? const <TaskItem>[])
            if (x.id == t.id) x.copyWith(done: !x.done) else x,
        ]);
    final userId = ref.read(authProvider).userId;
    // Write the flip through, or reopening reads the cache and un-ticks it.
    unawaited(DataCache.saveTasks(userId, _tasks ?? []));
    try {
      await _api.setTaskDone(t.id, !t.done);
    } on NotFoundException {
      // The task is gone; there is nothing left to tick.
    } on SessionExpiredException {
      // Signing out already.
    } catch (_) {
      await Outbox.add(
        userId,
        OutboxOp(kind: OutboxKind.setTaskDone, id: t.id, done: !t.done),
      );
    }
  }

  Future<void> _delete(TaskItem t) async {
    setState(() => _tasks = _tasks?.where((x) => x.id != t.id).toList());
    final userId = ref.read(authProvider).userId;
    unawaited(DataCache.saveTasks(userId, _tasks ?? []));
    try {
      await _api.deleteTask(t.id);
    } on NotFoundException {
      // Already gone.
    } on SessionExpiredException {
      // Signing out already.
    } catch (_) {
      await Outbox.add(userId, OutboxOp(kind: OutboxKind.deleteTask, id: t.id));
      if (!mounted) return;
      dataSnack(context, "Deleted. Will sync when you're back online.");
    }
  }

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
        onRefresh: _load,
        child: _content(),
      ),
    );
  }

  Widget _content() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error) {
      return _scrollable(DataErrorState(onRetry: _load));
    }
    final tasks = _tasks ?? const [];
    if (tasks.isEmpty) {
      return _scrollable(const DataEmptyState(
        icon: Icons.alarm_rounded,
        gradient: Aurora.gradAmber,
        label: 'No reminders yet.\nSay "remind me…" to add one.',
      ));
    }
    final overdue = _overdueCount;
    final children = <Widget>[
      DataCountHeader(
        text: '${tasks.length} ${tasks.length == 1 ? "reminder" : "reminders"}'
            '${overdue > 0 ? " · $overdue overdue" : ""}',
        accent: overdue > 0 ? Aurora.danger : Aurora.amber,
      ),
    ];
    for (final g in groupTasksByDate(tasks)) {
      children.add(_GroupHeader(label: g.label, count: g.items.length));
      final accent = _groupColor(g.label);
      children.addAll(g.items.map((t) => _tile(t, accent)));
    }
    return ListView(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 28), children: children);
  }

  Widget _tile(TaskItem t, Color accent) {
    final due = dataDueLabel(t.dueDate);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: SwipeToDelete(
        dismissKey: ValueKey('task-${t.id}'),
        confirm: () => confirmAction(
          context,
          title: 'Delete reminder?',
          message: t.title,
        ),
        onDismissed: () => _delete(t),
        child: Container(
          padding: const EdgeInsets.fromLTRB(12, 12, 14, 12),
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
                onTap: () => _toggle(t),
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
                        decoration:
                            t.done ? TextDecoration.lineThrough : null,
                      ),
                    ),
                    if (due != null) ...[
                      const SizedBox(height: 7),
                      _DuePill(
                          label: due,
                          accent: t.done ? Aurora.textMuted : accent),
                    ],
                  ],
                ),
              ),
            ],
          ),
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
