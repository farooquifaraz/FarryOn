import 'package:flutter/material.dart';

import '../../../data/live_client.dart';
import '../../../protocol/protocol.dart';

/// Compact pill showing connection status and the assistant's conversational
/// state (idle / listening / thinking / speaking).
class StatusIndicator extends StatelessWidget {
  const StatusIndicator({
    super.key,
    required this.connection,
    required this.liveState,
    required this.deviceKind,
  });

  final ConnectionStatus connection;
  final LiveState liveState;
  final String deviceKind;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (label, color, icon) = _stateVisual(theme);

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _Pill(
          color: _connectionColor(theme),
          icon: _connectionIcon(),
          label: _connectionLabel(),
        ),
        const SizedBox(width: 8),
        _Pill(color: color, icon: icon, label: label),
        const SizedBox(width: 8),
        _Pill(
          color: theme.colorScheme.surfaceContainerHighest,
          icon: deviceKind == 'glasses' ? Icons.visibility : Icons.smartphone,
          label: deviceKind,
          foreground: theme.colorScheme.onSurface,
        ),
      ],
    );
  }

  (String, Color, IconData) _stateVisual(ThemeData theme) {
    switch (liveState) {
      case LiveState.listening:
        return ('Listening', Colors.green, Icons.mic);
      case LiveState.thinking:
        return ('Thinking', Colors.amber.shade700, Icons.psychology);
      case LiveState.speaking:
        return ('Speaking', theme.colorScheme.primary, Icons.graphic_eq);
      case LiveState.idle:
        return ('Idle', theme.colorScheme.outline, Icons.circle_outlined);
    }
  }

  Color _connectionColor(ThemeData theme) => switch (connection) {
        ConnectionStatus.connected => Colors.green,
        ConnectionStatus.connecting => Colors.amber.shade700,
        ConnectionStatus.reconnecting => Colors.orange,
        ConnectionStatus.disconnected => theme.colorScheme.error,
      };

  IconData _connectionIcon() => switch (connection) {
        ConnectionStatus.connected => Icons.cloud_done,
        ConnectionStatus.connecting => Icons.cloud_sync,
        ConnectionStatus.reconnecting => Icons.cloud_sync,
        ConnectionStatus.disconnected => Icons.cloud_off,
      };

  String _connectionLabel() => switch (connection) {
        ConnectionStatus.connected => 'Online',
        ConnectionStatus.connecting => 'Connecting',
        ConnectionStatus.reconnecting => 'Reconnecting',
        ConnectionStatus.disconnected => 'Offline',
      };
}

class _Pill extends StatelessWidget {
  const _Pill({
    required this.color,
    required this.icon,
    required this.label,
    this.foreground = Colors.white,
  });

  final Color color;
  final IconData icon;
  final String label;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: foreground),
          const SizedBox(width: 5),
          Text(
            label,
            style: TextStyle(
              color: foreground,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
