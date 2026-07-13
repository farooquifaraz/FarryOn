import 'package:flutter/material.dart';

import '../../core/chat_history.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import 'data_common.dart';

/// Past conversations, newest first. Tap one to read the full transcript.
class ConversationsScreen extends StatefulWidget {
  const ConversationsScreen({super.key});

  static Future<void> open(BuildContext context) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const ConversationsScreen()),
      );

  @override
  State<ConversationsScreen> createState() => _ConversationsScreenState();
}

class _ConversationsScreenState extends State<ConversationsScreen> {
  late Future<List<ChatSession>> _future = ChatHistoryStore.load();

  void _reload() => setState(() => _future = ChatHistoryStore.load());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        title: const Row(
          children: [
            GradientIcon(Icons.forum_rounded,
                gradient: Aurora.gradPurple, size: 22),
            SizedBox(width: 10),
            Text('Conversations'),
          ],
        ),
      ),
      body: FutureBuilder<List<ChatSession>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          final sessions = snap.data ?? const [];
          if (sessions.isEmpty) {
            return const Center(
              child: DataEmptyState(
                icon: Icons.forum_rounded,
                gradient: Aurora.gradPurple,
                label: 'No saved conversations yet.\n'
                    'Chats are saved when you end a session.',
              ),
            );
          }
          return Column(
            children: [
              Align(
                alignment: Alignment.centerRight,
                child: Padding(
                  padding: const EdgeInsets.only(right: 8, top: 4),
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
              ),
              Expanded(
                child: ListView.separated(
                  padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
                  itemCount: sessions.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 10),
                  itemBuilder: (context, i) {
                    final s = sessions[i];
                    return DataCard(
                      padding: const EdgeInsets.all(12),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(10),
                        onTap: () => ChatSessionScreen.open(context, s),
                        child: Row(
                          children: [
                            const GradientIconTile(Icons.forum_rounded,
                                gradient: Aurora.gradPurple,
                                tileSize: 36,
                                iconSize: 19),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(s.preview,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                          color: Aurora.textPrimary)),
                                  const SizedBox(height: 2),
                                  Text(
                                    '${dataWhen(s.startedAt)} · ${s.lines.length} messages',
                                    style: const TextStyle(
                                        color: Aurora.textMuted, fontSize: 12),
                                  ),
                                ],
                              ),
                            ),
                            const Icon(Icons.chevron_right_rounded,
                                color: Aurora.textMuted),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

/// Read-only view of one saved conversation (chat bubbles).
class ChatSessionScreen extends StatelessWidget {
  const ChatSessionScreen({super.key, required this.session});
  final ChatSession session;

  static Future<void> open(BuildContext context, ChatSession session) =>
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ChatSessionScreen(session: session),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: Text(dataWhen(session.startedAt))),
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
                gradient: isUser ? Aurora.gradTeal : null,
                color: isUser ? null : Aurora.glass,
                borderRadius: BorderRadius.circular(14),
                border: isUser
                    ? null
                    : Border.all(color: Aurora.glassBorder),
              ),
              child: Column(
                crossAxisAlignment:
                    isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
                children: [
                  Text(
                    isUser ? 'You' : 'Farry',
                    style: TextStyle(
                      color: isUser ? Aurora.tealInk : Aurora.purpleSoft,
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(l.text,
                      style: TextStyle(
                          color: isUser ? Aurora.tealInk : Aurora.textPrimary,
                          height: 1.35)),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
