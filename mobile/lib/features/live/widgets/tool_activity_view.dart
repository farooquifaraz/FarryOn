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
  });

  final List<ToolActivity> tools;

  /// Called when the user grants/denies a permission-gated tool call.
  final void Function(String id, bool granted)? onPermission;

  @override
  Widget build(BuildContext context) {
    if (tools.isEmpty) return const SizedBox.shrink();
    // Most-recent first.
    final ordered = tools.reversed.toList(growable: false);
    return SizedBox(
      height: 92,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: ordered.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (context, i) =>
            _ToolCard(activity: ordered[i], onPermission: onPermission),
      ),
    );
  }
}

class _ToolCard extends StatelessWidget {
  const _ToolCard({required this.activity, this.onPermission});

  final ToolActivity activity;
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
      default:
        return _ToolSpec(name, Icons.build, (a) => a.toString());
    }
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
