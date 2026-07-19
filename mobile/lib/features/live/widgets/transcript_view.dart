import 'package:flutter/material.dart';

import '../../../core/theme.dart';
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
                color: Aurora.textMuted,
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

/// The app's own voice: full width, amber, no speaker name and no avatar.
///
/// Deliberately unlike both bubbles. It usually sits directly under Farry
/// saying the opposite ("OK, I've set a reminder"), and the whole job of this
/// line is to be believed over hers.
Widget _notice(ThemeData theme, String text) => Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(vertical: 5),
      padding: const EdgeInsets.fromLTRB(12, 9, 12, 10),
      decoration: BoxDecoration(
        color: Aurora.amber.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Aurora.amber.withValues(alpha: 0.5)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.notifications_off_rounded,
              size: 17, color: Aurora.amber),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              text,
              style: theme.textTheme.bodySmall?.copyWith(
                color: Aurora.textPrimary,
                height: 1.34,
              ),
            ),
          ),
        ],
      ),
    );

class _Bubble extends StatelessWidget {
  const _Bubble({required this.entry});

  final TranscriptEntry entry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (entry.isNotice) return _notice(theme, entry.text);
    final isUser = entry.isUser;
    final streaming = !entry.isFinal;

    final bubble = Container(
      padding: const EdgeInsets.fromLTRB(14, 9, 14, 10),
      constraints: BoxConstraints(
        maxWidth: MediaQuery.of(context).size.width * 0.74,
      ),
      decoration: BoxDecoration(
        // User bubbles get a teal-tinted fill; FarryOn a neutral glass — so the
        // two voices read apart at a glance.
        color: isUser
            ? Aurora.teal.withValues(alpha: 0.22)
            : Colors.white.withValues(alpha: 0.07),
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(18),
          topRight: const Radius.circular(18),
          bottomLeft: Radius.circular(isUser ? 18 : 6),
          bottomRight: Radius.circular(isUser ? 6 : 18),
        ),
        border: Border.all(
          color: isUser
              ? Aurora.teal.withValues(alpha: 0.45)
              : Colors.white.withValues(alpha: 0.12),
        ),
      ),
      child: Column(
        crossAxisAlignment:
            isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            isUser ? 'You' : 'Farry',
            style: theme.textTheme.labelSmall?.copyWith(
              color: isUser ? Aurora.mint : Aurora.tealInk,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.2,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            entry.text.isEmpty ? '…' : entry.text,
            style: theme.textTheme.bodyMedium?.copyWith(
              color: streaming ? Aurora.textMuted : Aurora.textPrimary,
              height: 1.36,
              fontStyle: streaming ? FontStyle.italic : FontStyle.normal,
            ),
          ),
        ],
      ),
    );

    // FarryOn lines get a small glowing avatar dot on the left.
    final row = Row(
      mainAxisAlignment:
          isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        if (!isUser) ...[
          const _AvatarDot(),
          const SizedBox(width: 7),
        ],
        Flexible(child: bubble),
      ],
    );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: row,
    );
  }
}

/// Tiny glowing teal dot that marks FarryOn's lines.
class _AvatarDot extends StatelessWidget {
  const _AvatarDot();

  @override
  Widget build(BuildContext context) => Container(
        width: 22,
        height: 22,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: const RadialGradient(
            colors: [Aurora.mint, Aurora.teal],
          ),
          boxShadow: [
            BoxShadow(
              color: Aurora.teal.withValues(alpha: 0.5),
              blurRadius: 8,
            ),
          ],
        ),
        child: const Icon(Icons.auto_awesome, size: 12, color: Colors.white),
      );
}
