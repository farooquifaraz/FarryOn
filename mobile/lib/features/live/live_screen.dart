import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../capture/device_registry.dart';
import '../../protocol/protocol.dart';
import '../../state/live_state.dart';
import '../../state/permissions.dart';
import '../../state/providers.dart';
import 'widgets/camera_preview_view.dart';
import 'widgets/status_indicator.dart';
import 'widgets/tool_activity_view.dart';
import 'widgets/transcript_view.dart';

/// The single primary screen: camera preview, status, transcripts, tool
/// activity, and the mic / interrupt / text controls.
class LiveScreen extends ConsumerStatefulWidget {
  const LiveScreen({super.key});

  @override
  ConsumerState<LiveScreen> createState() => _LiveScreenState();
}

class _LiveScreenState extends ConsumerState<LiveScreen>
    with WidgetsBindingObserver {
  final _textController = TextEditingController();
  bool _connectRequested = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Kick off connection after first frame so providers are ready.
    WidgetsBinding.instance.addPostFrameCallback((_) => _connect());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _textController.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_connectRequested) return;
    _connectRequested = true;
    final outcome = await ref.read(liveProvider.notifier).connect();
    if (outcome != PermissionOutcome.granted && mounted) {
      _showPermissionDialog(outcome);
    }
  }

  void _showPermissionDialog(PermissionOutcome outcome) {
    final permanent = outcome == PermissionOutcome.permanentlyDenied;
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Permissions needed'),
        content: Text(
          permanent
              ? 'FarryOn needs the microphone and camera. Please enable them in '
                  'Settings to use voice and vision.'
              : 'FarryOn needs the microphone and camera to see and hear. '
                  'Please allow access.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Not now'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.pop(context);
              if (permanent) {
                await ref.read(permissionsProvider).openSettings();
              } else {
                _connectRequested = false;
                _connect();
              }
            },
            child: Text(permanent ? 'Open settings' : 'Allow'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(liveProvider);
    final notifier = ref.read(liveProvider.notifier);

    // Surface transient errors as a snackbar.
    ref.listen<LiveSessionState>(liveProvider, (prev, next) {
      final err = next.lastError;
      if (err != null && err != prev?.lastError) {
        ScaffoldMessenger.of(context)
          ..clearSnackBars()
          ..showSnackBar(SnackBar(content: Text(err)));
        notifier.dismissError();
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: const Text('FarryOn'),
        actions: [
          IconButton(
            tooltip: 'Switch device',
            icon: Icon(
              state.deviceKind == 'glasses'
                  ? Icons.visibility
                  : Icons.smartphone,
            ),
            onPressed: () => _showDeviceSheet(notifier, state),
          ),
          IconButton(
            tooltip: 'Backend settings',
            icon: const Icon(Icons.settings),
            onPressed: _showSettingsSheet,
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(8),
              child: StatusIndicator(
                connection: state.connection,
                liveState: state.liveState,
                deviceKind: state.deviceKind,
              ),
            ),
            // Camera preview.
            Expanded(
              flex: 3,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: CameraPreviewView(
                  source: notifier.activeSource,
                  enabled: state.cameraOn,
                ),
              ),
            ),
            const SizedBox(height: 8),
            // Tool activity.
            ToolActivityView(
              tools: state.tools,
              onPermission: notifier.respondToolPermission,
            ),
            // Transcripts.
            Expanded(
              flex: 4,
              child: TranscriptView(entries: state.transcripts),
            ),
            _Controls(
              state: state,
              textController: _textController,
              onMicToggle: notifier.toggleMic,
              onInterrupt: notifier.interrupt,
              onSendText: (text) {
                notifier.sendText(text);
                _textController.clear();
              },
              onToggleCamera: () =>
                  notifier.setCameraEnabled(!state.cameraOn),
            ),
          ],
        ),
      ),
    );
  }

  void _showDeviceSheet(LiveNotifier notifier, LiveSessionState state) {
    showModalBottomSheet<void>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const ListTile(
              title: Text('Capture device'),
              subtitle: Text('Universal adapter — phone or smart glasses'),
            ),
            for (final kind in CaptureDeviceKind.values)
              RadioListTile<CaptureDeviceKind>(
                value: kind,
                groupValue: _kindFromName(state.deviceKind),
                title: Text(_deviceLabel(kind)),
                subtitle: kind == CaptureDeviceKind.glasses
                    ? const Text('Stub — BLE/RTSP transport TODO')
                    : null,
                onChanged: (value) {
                  if (value != null) notifier.switchDevice(value);
                  Navigator.pop(context);
                },
              ),
          ],
        ),
      ),
    );
  }

  void _showSettingsSheet() {
    final current = ref.read(configProvider);
    final hostCtl = TextEditingController(text: current.host);
    final portCtl = TextEditingController(text: current.port.toString());
    var secure = current.secure;

    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => Padding(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(context).viewInsets.bottom,
          left: 16,
          right: 16,
          top: 16,
        ),
        child: StatefulBuilder(
          builder: (context, setSheet) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Backend', style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 12),
              TextField(
                controller: hostCtl,
                decoration: const InputDecoration(
                  labelText: 'Host',
                  hintText: 'e.g. 10.0.2.2 or example.com',
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: portCtl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Port'),
              ),
              SwitchListTile(
                value: secure,
                title: const Text('Use TLS (wss://)'),
                onChanged: (v) => setSheet(() => secure = v),
                contentPadding: EdgeInsets.zero,
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerRight,
                child: FilledButton(
                  onPressed: () {
                    final port =
                        int.tryParse(portCtl.text.trim()) ?? current.port;
                    ref.read(configProvider.notifier).state = current.copyWith(
                      host: hostCtl.text.trim(),
                      port: port,
                      secure: secure,
                    );
                    Navigator.pop(context);
                  },
                  child: const Text('Save & reconnect'),
                ),
              ),
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
    );
  }

  static CaptureDeviceKind _kindFromName(String name) =>
      name == 'glasses' ? CaptureDeviceKind.glasses : CaptureDeviceKind.phone;

  static String _deviceLabel(CaptureDeviceKind kind) => switch (kind) {
        CaptureDeviceKind.phone => 'Phone (camera + mic)',
        CaptureDeviceKind.glasses => 'Smart glasses',
      };
}

/// Bottom control bar: camera toggle, push-to-talk mic, interrupt, and a text
/// input for typed turns.
class _Controls extends StatelessWidget {
  const _Controls({
    required this.state,
    required this.textController,
    required this.onMicToggle,
    required this.onInterrupt,
    required this.onSendText,
    required this.onToggleCamera,
  });

  final LiveSessionState state;
  final TextEditingController textController;
  final VoidCallback onMicToggle;
  final VoidCallback onInterrupt;
  final ValueChanged<String> onSendText;
  final VoidCallback onToggleCamera;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final speaking = state.liveState == LiveState.speaking;

    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.06),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              IconButton.filledTonal(
                tooltip: state.cameraOn ? 'Turn camera off' : 'Turn camera on',
                onPressed: onToggleCamera,
                icon: Icon(
                  state.cameraOn ? Icons.videocam : Icons.videocam_off,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _MicButton(
                  micOpen: state.micOpen,
                  enabled: state.permissionsGranted,
                  onPressed: onMicToggle,
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                tooltip: 'Interrupt',
                onPressed: speaking ? onInterrupt : null,
                style: IconButton.styleFrom(
                  backgroundColor:
                      speaking ? theme.colorScheme.errorContainer : null,
                ),
                icon: const Icon(Icons.stop),
              ),
            ],
          ),
          const SizedBox(height: 8),
          TextField(
            controller: textController,
            textInputAction: TextInputAction.send,
            onSubmitted: (text) {
              if (text.trim().isNotEmpty) onSendText(text);
            },
            decoration: InputDecoration(
              hintText: 'Type a message…',
              isDense: true,
              border: const OutlineInputBorder(),
              suffixIcon: IconButton(
                icon: const Icon(Icons.send),
                onPressed: () {
                  final text = textController.text;
                  if (text.trim().isNotEmpty) onSendText(text);
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MicButton extends StatelessWidget {
  const _MicButton({
    required this.micOpen,
    required this.enabled,
    required this.onPressed,
  });

  final bool micOpen;
  final bool enabled;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return FilledButton.icon(
      onPressed: enabled ? onPressed : null,
      style: FilledButton.styleFrom(
        backgroundColor: micOpen ? Colors.red : null,
        padding: const EdgeInsets.symmetric(vertical: 14),
      ),
      icon: Icon(micOpen ? Icons.mic : Icons.mic_none),
      label: Text(micOpen ? 'Listening — tap to stop' : 'Tap to talk'),
    );
  }
}
