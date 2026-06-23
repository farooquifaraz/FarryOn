import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../data/live_client.dart';
import '../../../protocol/protocol.dart';

/// Soft, translucent status pills (Midnight Aurora): connection, the
/// assistant's conversational state, and the active capture device.
class StatusIndicator extends StatelessWidget {
  const StatusIndicator({
    super.key,
    required this.connection,
    required this.liveState,
    required this.deviceKind,
    this.connectionOnly = false,
  });

  final ConnectionStatus connection;
  final LiveState liveState;
  final String deviceKind;

  /// When true, render ONLY the connection pill (used in the cramped top bar so
  /// the status is never clipped). The conversational state is shown by the orb
  /// and the device by the settings sheet, so they're omitted there.
  final bool connectionOnly;

  @override
  Widget build(BuildContext context) {
    final connectionPill = _Pill(
      color: _connectionColor(),
      icon: _connectionIcon(),
      label: _connectionLabel(),
    );
    if (connectionOnly) return connectionPill;

    final (label, color, icon) = _stateVisual();
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        connectionPill,
        const SizedBox(width: 8),
        _Pill(color: color, icon: icon, label: label),
        const SizedBox(width: 8),
        _Pill(
          color: Aurora.textMuted,
          icon: deviceKind == 'glasses' ? Icons.visibility : Icons.smartphone,
          label: deviceKind,
        ),
      ],
    );
  }

  (String, Color, IconData) _stateVisual() {
    switch (liveState) {
      case LiveState.listening:
        return ('Listening', Aurora.mint, Icons.mic);
      case LiveState.thinking:
        return ('Thinking', Aurora.amber, Icons.auto_awesome);
      case LiveState.speaking:
        return ('Speaking', Aurora.teal, Icons.graphic_eq);
      case LiveState.idle:
        return ('Idle', Aurora.textMuted, Icons.circle_outlined);
    }
  }

  Color _connectionColor() => switch (connection) {
        ConnectionStatus.connected => Aurora.teal,
        ConnectionStatus.connecting => Aurora.amber,
        ConnectionStatus.reconnecting => Aurora.amber,
        ConnectionStatus.disconnected => Aurora.danger,
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
  const _Pill({required this.color, required this.icon, required this.label});

  final Color color;
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
      decoration: BoxDecoration(
        color: Aurora.tint(color, 0.14),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Aurora.tint(color, 0.25)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
