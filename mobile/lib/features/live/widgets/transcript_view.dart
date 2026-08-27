import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../state/live_state.dart';

/// `HH:mm:ss` for the bubble stamps.
String _hhmmss(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:'
    '${t.minute.toString().padLeft(2, '0')}:'
    '${t.second.toString().padLeft(2, '0')}';

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
      itemBuilder: (context, i) {
        final entry = widget.entries[i];
        // Date separator whenever the calendar day changes between lines —
        // the familiar chat-app convention, so a conversation that crossed
        // midnight (or a restored history) still reads unambiguously.
        final showDate = i == 0 || !_sameDay(widget.entries[i - 1].time, entry.time);
        final bubble = _Bubble(entry: entry);
        if (!showDate) return bubble;
        return Column(
          children: [_DateChip(date: entry.time), bubble],
        );
      },
    );
  }

  static bool _sameDay(DateTime a, DateTime b) =>
      a.year == b.year && a.month == b.month && a.day == b.day;
}

/// Centered "Today" / "12 Aug 2026" pill shown when the day changes.
class _DateChip extends StatelessWidget {
  const _DateChip({required this.date});

  final DateTime date;

  static const _months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];

  String _label() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final day = DateTime(date.year, date.month, date.day);
    if (day == today) return 'Today';
    if (day == today.subtract(const Duration(days: 1))) return 'Yesterday';
    return '${date.day} ${_months[date.month - 1]} ${date.year}';
  }

  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.08),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Text(
            _label(),
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: Aurora.textMuted,
                  fontWeight: FontWeight.w600,
                ),
          ),
        ),
      );
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
          Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(
                isUser ? 'You' : 'Farry',
                style: theme.textTheme.labelSmall?.copyWith(
                  color: isUser ? Aurora.mint : Aurora.tealInk,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.2,
                ),
              ),
              const SizedBox(width: 6),
              // Seconds included ON PURPOSE: a user line's time is the moment
              // Farry began hearing it and the reply's time is the moment she
              // began answering, so adjacent stamps read as her actual
              // latency — minutes alone would hide it (user-asked 2026-08-27).
              Text(
                _hhmmss(entry.time),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: Aurora.textMuted,
                  fontSize: 10,
                  fontFeatures: const [FontFeature.tabularFigures()],
                ),
              ),
            ],
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
