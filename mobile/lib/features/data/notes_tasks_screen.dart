import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/chat_history.dart';
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
  late final TabController _tabs = TabController(length: 3, vsync: this);

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
          tabs: const [
            Tab(text: 'Notes'),
            Tab(text: 'Tasks'),
            Tab(text: 'History'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _NotesTab(api: api),
          _TasksTab(api: api),
          const _HistoryTab(),
        ],
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
          final children = <Widget>[];
          for (final g in _groupByDate(tasks)) {
            children.add(_GroupHeader(label: g.label, count: g.items.length));
            children.addAll(g.items.map(_tile));
          }
          return ListView(padding: const EdgeInsets.all(12), children: children);
        },
      ),
    );
  }

  Widget _tile(TaskItem t) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: _Card(
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
                        decoration:
                            t.done ? TextDecoration.lineThrough : null,
                      ),
                    ),
                    if (_dueLabel(t.dueDate) != null)
                      Text(
                        _dueLabel(t.dueDate)!,
                        style: const TextStyle(
                            color: Aurora.textMuted, fontSize: 12),
                      ),
                  ],
                ),
              ),
              IconButton(
                icon: const Icon(Icons.delete_outline, color: Aurora.textMuted),
                onPressed: () async {
                  await widget.api.deleteTask(t.id);
                  _reload();
                },
              ),
            ],
          ),
        ),
      );
}

/// A short, friendly time label for a task's ISO due date.
String? _dueLabel(String? iso) {
  if (iso == null || iso.isEmpty) return null;
  final d = DateTime.tryParse(iso)?.toLocal();
  if (d == null) return iso;
  final h = d.hour.toString().padLeft(2, '0');
  final m = d.minute.toString().padLeft(2, '0');
  return 'due ${d.day}/${d.month} $h:$m';
}

/// Bucket tasks by due date for grouped display.
List<({String label, List<TaskItem> items})> _groupByDate(
    List<TaskItem> tasks) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final tomorrow = today.add(const Duration(days: 1));
  final weekEnd = today.add(const Duration(days: 7));
  final order = ['Overdue', 'Today', 'Tomorrow', 'This week', 'Later',
      'No date'];
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

class _GroupHeader extends StatelessWidget {
  const _GroupHeader({required this.label, required this.count});
  final String label;
  final int count;
  @override
  Widget build(BuildContext context) {
    final danger = label == 'Overdue';
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 8, 4, 8),
      child: Row(
        children: [
          Text(
            label.toUpperCase(),
            style: TextStyle(
              color: danger ? Aurora.danger : Aurora.mint,
              fontSize: 12,
              fontWeight: FontWeight.w600,
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

/// Past conversations, newest first. Tap one to read the full transcript.
class _HistoryTab extends StatefulWidget {
  const _HistoryTab();
  @override
  State<_HistoryTab> createState() => _HistoryTabState();
}

class _HistoryTabState extends State<_HistoryTab> {
  late Future<List<ChatSession>> _future = ChatHistoryStore.load();

  void _reload() => setState(() => _future = ChatHistoryStore.load());

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<ChatSession>>(
      future: _future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        final sessions = snap.data ?? const [];
        if (sessions.isEmpty) {
          return const _EmptyState(
            icon: Icons.forum_outlined,
            label: 'No saved conversations yet.\n'
                'Chats are saved when you end a session.',
          );
        }
        return Column(
          children: [
            Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                icon: const Icon(Icons.delete_sweep_outlined,
                    size: 18, color: Aurora.textMuted),
                label: const Text('Clear all',
                    style: TextStyle(color: Aurora.textMuted)),
                onPressed: () async {
                  await ChatHistoryStore.clear();
                  _reload();
                },
              ),
            ),
            Expanded(
              child: ListView.separated(
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
                itemCount: sessions.length,
                separatorBuilder: (_, __) => const SizedBox(height: 8),
                itemBuilder: (context, i) {
                  final s = sessions[i];
                  return _Card(
                    child: ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(
                        s.preview,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Aurora.textPrimary),
                      ),
                      subtitle: Text(
                        '${_when(s.startedAt)} · ${s.lines.length} messages',
                        style: const TextStyle(
                            color: Aurora.textMuted, fontSize: 12),
                      ),
                      trailing: const Icon(Icons.chevron_right,
                          color: Aurora.textMuted),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => _ChatSessionScreen(session: s),
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

/// Read-only view of one saved conversation.
class _ChatSessionScreen extends StatelessWidget {
  const _ChatSessionScreen({required this.session});
  final ChatSession session;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: Text(_when(session.startedAt))),
      body: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: session.lines.length,
        itemBuilder: (context, i) {
          final l = session.lines[i];
          final isUser = l.role == 'user';
          return Align(
            alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
            child: Container(
              margin: const EdgeInsets.symmetric(vertical: 4),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.78,
              ),
              decoration: BoxDecoration(
                color: isUser
                    ? Aurora.teal.withValues(alpha: 0.20)
                    : Aurora.glass,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: Aurora.glassBorder),
              ),
              child: Column(
                crossAxisAlignment: isUser
                    ? CrossAxisAlignment.end
                    : CrossAxisAlignment.start,
                children: [
                  Text(
                    isUser ? 'You' : 'FarryOn',
                    style: TextStyle(
                      color: isUser ? Aurora.mint : Aurora.tealInk,
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(l.text,
                      style: const TextStyle(
                          color: Aurora.textPrimary, height: 1.35)),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}

/// "21 Jun, 17:43"-style label for a saved conversation.
String _when(DateTime d) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  final h = d.hour.toString().padLeft(2, '0');
  final m = d.minute.toString().padLeft(2, '0');
  return '${d.day} ${months[d.month - 1]}, $h:$m';
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
