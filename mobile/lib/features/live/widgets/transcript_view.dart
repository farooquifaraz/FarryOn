import 'package:flutter/material.dart';

import '../../../state/live_state.dart';

/// Scrolling list of user + assistant transcript lines.
///
/// User lines align right, assistant lines left; non-final fragments are shown
/// slightly dimmed so streaming partials read as "in progress".
class TranscriptView extends StatefulWidget {
  const TranscriptView({super.key, required this.entries});

  final List<TranscriptEntry> entries;

  @override
  State<TranscriptView> createState() => _TranscriptViewState();
}

class _TranscriptViewState extends State<TranscriptView> {
  final _scrollController = ScrollController();

  @override
  void didUpdateWidget(TranscriptView oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Keep the newest line in view as transcripts stream in.
    if (widget.entries.length != oldWidget.entries.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (widget.entries.isEmpty) {
      return Center(
        child: Text(
          'Say something or type below to start.',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.outline,
              ),
        ),
      );
    }

    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.all(12),
      itemCount: widget.entries.length,
      itemBuilder: (context, i) => _Bubble(entry: widget.entries[i]),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.entry});

  final TranscriptEntry entry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isUser = entry.isUser;
    final bg = isUser
        ? theme.colorScheme.primaryContainer
        : theme.colorScheme.surfaceContainerHighest;
    final fg = isUser
        ? theme.colorScheme.onPrimaryContainer
        : theme.colorScheme.onSurface;

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(isUser ? 16 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 16),
          ),
        ),
        child: Column(
          crossAxisAlignment:
              isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            Text(
              isUser ? 'You' : 'FarryOn',
              style: theme.textTheme.labelSmall?.copyWith(
                color: fg.withValues(alpha: 0.7),
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 2),
            Opacity(
              opacity: entry.isFinal ? 1.0 : 0.7,
              child: Text(
                entry.text.isEmpty ? '…' : entry.text,
                style: theme.textTheme.bodyMedium?.copyWith(color: fg),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
