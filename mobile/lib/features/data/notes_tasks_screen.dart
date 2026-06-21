import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../data/data_api.dart';
import '../../state/providers.dart';

/// Shows the notes and tasks the assistant has saved, with delete and
/// mark-done. Reads them over REST from the same backend the live session uses.
class NotesTasksScreen extends ConsumerStatefulWidget {
  const NotesTasksScreen({super.key});

  @override
  ConsumerState<NotesTasksScreen> createState() => _NotesTasksScreenState();
}

class _NotesTasksScreenState extends ConsumerState<NotesTasksScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final api = ref.read(dataApiProvider);
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        title: const Text('Notes & Tasks'),
        bottom: TabBar(
          controller: _tabs,
          indicatorColor: Aurora.teal,
          labelColor: Aurora.textPrimary,
          unselectedLabelColor: Aurora.textMuted,
          tabs: const [Tab(text: 'Notes'), Tab(text: 'Tasks')],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [_NotesTab(api: api), _TasksTab(api: api)],
      ),
    );
  }
}

class _NotesTab extends StatefulWidget {
  const _NotesTab({required this.api});
  final DataApi api;
  @override
  State<_NotesTab> createState() => _NotesTabState();
}

class _NotesTabState extends State<_NotesTab> {
  late Future<List<NoteItem>> _future = widget.api.notes();

  void _reload() => setState(() => _future = widget.api.notes());

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: () async => _reload(),
      child: FutureBuilder<List<NoteItem>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return _ErrorState(message: '${snap.error}', onRetry: _reload);
          }
          final notes = snap.data ?? const [];
          if (notes.isEmpty) {
            return const _EmptyState(
              icon: Icons.sticky_note_2_outlined,
              label: 'No notes yet.\nSay "remember…" to add one.',
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: notes.length,
            separatorBuilder: (_, __) => const SizedBox(height: 8),
            itemBuilder: (context, i) {
              final n = notes[i];
              return _Card(
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        n.text,
                        style: const TextStyle(color: Aurora.textPrimary),
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.delete_outline,
                          color: Aurora.textMuted),
                      onPressed: () async {
                        await widget.api.deleteNote(n.id);
                        _reload();
                      },
                    ),
                  ],
                ),
              );
            },
          );
        },
      ),
    );
  }
}

class _TasksTab extends StatefulWidget {
  const _TasksTab({required this.api});
  final DataApi api;
  @override
  State<_TasksTab> createState() => _TasksTabState();
}

class _TasksTabState extends State<_TasksTab> {
  late Future<List<TaskItem>> _future = widget.api.tasks();

  void _reload() => setState(() => _future = widget.api.tasks());

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: () async => _reload(),
      child: FutureBuilder<List<TaskItem>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return _ErrorState(message: '${snap.error}', onRetry: _reload);
          }
          final tasks = snap.data ?? const [];
          if (tasks.isEmpty) {
            return const _EmptyState(
              icon: Icons.checklist_rtl,
              label: 'No tasks yet.\nSay "remind me…" to add one.',
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: tasks.length,
            separatorBuilder: (_, __) => const SizedBox(height: 8),
            itemBuilder: (context, i) {
              final t = tasks[i];
              return _Card(
                child: Row(
                  children: [
                    Checkbox(
                      value: t.done,
                      activeColor: Aurora.teal,
                      onChanged: (v) async {
                        await widget.api.setTaskDone(t.id, v ?? false);
                        _reload();
                      },
                    ),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            t.title,
                            style: TextStyle(
                              color: Aurora.textPrimary,
                              decoration: t.done
                                  ? TextDecoration.lineThrough
                                  : null,
                            ),
                          ),
                          if (t.dueDate != null && t.dueDate!.isNotEmpty)
                            Text(
                              'due ${t.dueDate}',
                              style: const TextStyle(
                                  color: Aurora.textMuted, fontSize: 12),
                            ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.delete_outline,
                          color: Aurora.textMuted),
                      onPressed: () async {
                        await widget.api.deleteTask(t.id);
                        _reload();
                      },
                    ),
                  ],
                ),
              );
            },
          );
        },
      ),
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(14, 6, 6, 6),
        decoration: BoxDecoration(
          color: Aurora.glass,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Aurora.glassBorder),
        ),
        child: child,
      );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.icon, required this.label});
  final IconData icon;
  final String label;
  @override
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: Aurora.textMuted),
            const SizedBox(height: 12),
            Text(label,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Aurora.textMuted)),
          ],
        ),
      );
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 40, color: Aurora.textMuted),
            const SizedBox(height: 10),
            const Text("Couldn't load — check the backend.",
                style: TextStyle(color: Aurora.textMuted)),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      );
}
