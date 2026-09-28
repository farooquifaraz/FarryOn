import 'dart:async';

import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../protocol/protocol.dart';
import '../../../state/live_state.dart';

/// Horizontal strip of tool-call cards (create_note / web_search / create_task /
/// send_message). Each card shows the tool, a human summary of its args, and a
/// pending/ok/error status; unknown tools fall back to a generic chip.
class ToolActivityView extends StatelessWidget {
  const ToolActivityView({
    super.key,
    required this.tools,
    this.onPermission,
    this.now = DateTime.now,
  });

  final List<ToolActivity> tools;

  /// Called when the user grants/denies a permission-gated tool call.
  final void Function(String id, bool granted)? onPermission;

  /// The clock the "Running… N s" counter reads (tests pass their own).
  final DateTime Function() now;

  @override
  Widget build(BuildContext context) {
    // Only show tools that are still running (or awaiting permission) — once a
    // task is done its card disappears instead of cluttering the screen.
    final active = tools
        .where((t) => t.isPending)
        .toList(growable: false)
        .reversed
        .toList(growable: false);
    if (active.isEmpty) return const SizedBox.shrink();
    return SizedBox(
      height: 72,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: active.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (context, i) => _ToolCard(
          activity: active[i],
          onPermission: onPermission,
          now: now,
        ),
      ),
    );
  }
}

class _ToolCard extends StatelessWidget {
  const _ToolCard({
    required this.activity,
    required this.now,
    this.onPermission,
  });

  final ToolActivity activity;
  final DateTime Function() now;
  final void Function(String id, bool granted)? onPermission;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final spec = _specFor(activity.name);
    final (statusColor, statusIcon, statusText) = _status(theme);

    return Container(
      width: 230,
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: Aurora.glass,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(spec.icon, size: 16, color: Aurora.mint),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  spec.label,
                  style: theme.textTheme.labelLarge,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              Icon(statusIcon, size: 16, color: statusColor),
            ],
          ),
          const SizedBox(height: 4),
          Expanded(
            child: Text(
              spec.summarize(activity.args),
              style: theme.textTheme.bodySmall,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (activity.needsPermission && activity.isPending)
            _PermissionButtons(
              onGrant: () => onPermission?.call(activity.id, true),
              onDeny: () => onPermission?.call(activity.id, false),
            )
          else if (activity.isPending && activity.startedAt != null)
            _RunningFor(
              since: activity.startedAt!,
              now: now,
              style: theme.textTheme.labelSmall?.copyWith(color: statusColor),
            )
          else
            Text(
              statusText,
              style: theme.textTheme.labelSmall?.copyWith(color: statusColor),
            ),
        ],
      ),
    );
  }

  (Color, IconData, String) _status(ThemeData theme) {
    if (activity.isPending) {
      return (Aurora.amber, Icons.hourglass_top, 'Running…');
    }
    if (activity.ok == true) {
      return (Aurora.mint, Icons.check_circle, 'Done');
    }
    return (
      Aurora.danger,
      Icons.error,
      activity.error ?? 'Failed',
    );
  }

  static _ToolSpec _specFor(String name) {
    switch (name) {
      case ToolName.createNote:
        return _ToolSpec(
          'Note',
          Icons.sticky_note_2,
          (a) => (a['text'] ?? '').toString(),
        );
      case ToolName.webSearch:
        return _ToolSpec(
          'Web search',
          Icons.search,
          (a) => (a['query'] ?? '').toString(),
        );
      case ToolName.createTask:
        return _ToolSpec('Task', Icons.checklist, (a) {
          final title = (a['title'] ?? '').toString();
          final due = a['due_date'];
          return due == null ? title : '$title (due $due)';
        });
      case ToolName.sendMessage:
        return _ToolSpec(
          'Message',
          Icons.send,
          (a) => 'To ${a['contact'] ?? '?'}: ${a['text'] ?? ''}',
        );
      case 'set_camera_zoom':
        return _ToolSpec(
          'Zoom',
          Icons.zoom_in,
          (a) => 'Zoom to ${a['level'] ?? '?'}x',
        );
      // A look through the camera is the one tool the user waits on in
      // silence: the glasses take the photo over Bluetooth and the server
      // then looks at it, and until now the card said only "Running…".
      // Say how long that usually takes, and count the seconds.
      case 'identify_image':
      case 'capture_photo':
        return _ToolSpec(
          'Looking',
          Icons.center_focus_strong,
          (a) => lookingHint,
        );
      case 'read_emails':
        return _ToolSpec('Reading inbox', Icons.inbox, (a) {
          final parts = [a['category'], a['range'], a['query']]
              .where((v) => v != null && v.toString().isNotEmpty)
              .join(' · ');
          return parts.isEmpty ? 'Latest emails' : parts;
        });
      case 'inbox_summary':
        return _ToolSpec(
          'Inbox summary',
          Icons.summarize,
          (a) => (a['range'] ?? 'today').toString(),
        );
      case 'read_email':
        return _ToolSpec(
          'Reading email',
          Icons.mark_email_unread,
          (a) => (a['query'] ?? (a['uid'] != null ? 'uid ${a['uid']}' : ''))
              .toString(),
        );
      case 'send_email':
        return _ToolSpec('Sending email', Icons.outgoing_mail, (a) {
          final reply = a['reply_to_uid'] != null ? 'Reply to ' : 'To ';
          final cc = a['cc'] != null ? ' (cc ${a['cc']})' : '';
          return '$reply${a['to'] ?? '?'}$cc: ${a['subject'] ?? ''}';
        });
      case 'forward_email':
        return _ToolSpec(
          'Forwarding email',
          Icons.forward_to_inbox,
          (a) => 'To ${a['to'] ?? '?'}',
        );
      case 'mark_email_read':
        return _ToolSpec(
          'Updating email',
          Icons.mark_email_read,
          (a) => a['unread'] == true ? 'Mark as unread' : 'Mark as read',
        );
      default:
        return _ToolSpec(name, Icons.build, (a) => a.toString());
    }
  }
}

/// What the "Looking" card says while a photo is taken and looked at.
/// The 5–15 s is the glasses' Bluetooth transfer as measured on the live
/// server (5.7 s on a good run, 16 s on a slow one, 2026-09-22); the look
/// itself adds a few seconds.
const String lookingHint =
    'Taking a photo and looking at it. From the glasses this usually takes '
    '5–15 seconds.';

/// "Running… 7 s": how long a pending tool has been at it, ticking once a
/// second so a slow photo visibly is still in progress rather than stuck.
class _RunningFor extends StatefulWidget {
  const _RunningFor({required this.since, required this.now, this.style});

  final DateTime since;
  final DateTime Function() now;
  final TextStyle? style;

  @override
  State<_RunningFor> createState() => _RunningForState();
}

class _RunningForState extends State<_RunningFor> {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final secs = widget.now().difference(widget.since).inSeconds;
    return Text(
      secs < 1 ? 'Running…' : 'Running… $secs s',
      style: widget.style,
    );
  }
}

class _ToolSpec {
  _ToolSpec(this.label, this.icon, this.summarize);
  final String label;
  final IconData icon;
  final String Function(Map<String, dynamic> args) summarize;
}

class _PermissionButtons extends StatelessWidget {
  const _PermissionButtons({required this.onGrant, required this.onDeny});

  final VoidCallback onGrant;
  final VoidCallback onDeny;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        TextButton(
          onPressed: onDeny,
          style: TextButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            minimumSize: const Size(0, 28),
          ),
          child: const Text('Deny'),
        ),
        const Spacer(),
        FilledButton(
          onPressed: onGrant,
          style: FilledButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            minimumSize: const Size(0, 28),
          ),
          child: const Text('Allow'),
        ),
      ],
    );
  }
}
