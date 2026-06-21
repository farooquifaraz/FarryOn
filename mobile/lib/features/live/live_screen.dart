import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../capture/device_registry.dart';
import '../../core/theme.dart';
import '../../protocol/protocol.dart';
import '../../state/live_state.dart';
import '../../state/permissions.dart';
import '../../state/providers.dart';
import '../data/notes_tasks_screen.dart';
import 'widgets/aurora_orb.dart';
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
  double _zoomBase = 1.0; // zoom level when a pinch gesture begins

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
                unawaited(_connect());
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

    // The camera is a large rounded "viewport" (a small inset + rounded
    // corners reads as intentional, not a raw edge-to-edge fill); everything
    // else floats on top with dark backing so it stays legible over any scene.
    return Scaffold(
      backgroundColor: Aurora.base,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(24),
            child: Stack(
        fit: StackFit.expand,
        children: [
          // 1. Camera fills the whole frame.
          CameraPreviewView(
            source: notifier.activeSource,
            enabled: state.cameraOn,
            portrait: state.cameraPortrait,
          ),
          // 2. Pinch-to-zoom anywhere on the camera (below the overlays, so
          //    taps on controls still win).
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onScaleStart: (_) => _zoomBase = state.cameraZoom,
              onScaleUpdate: (d) {
                if (d.pointerCount < 2) return;
                final z = (_zoomBase * d.scale).clamp(1.0, 8.0).toDouble();
                if ((z - state.cameraZoom).abs() > 0.05) {
                  notifier.setCameraZoom(z);
                }
              },
            ),
          ),
          // 3. Voice orb focal point.
          Center(
            child: IgnorePointer(child: AuroraOrb(state: state.liveState)),
          ),
          // 4. Top overlay: status + zoom read-out + actions.
          SafeArea(
            child: Align(
              alignment: Alignment.topCenter,
              child: _TopOverlay(
                state: state,
                onSettings: _showSettingsSheet,
                onOrientation: () =>
                    notifier.setCameraPortrait(!state.cameraPortrait),
                onNotes: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => const NotesTasksScreen(),
                  ),
                ),
              ),
            ),
          ),
          // 5. Zoom presets on the right edge.
          SafeArea(
            child: Align(
              alignment: Alignment.centerRight,
              child: _ZoomPresets(
                current: state.cameraZoom,
                onPick: notifier.setCameraZoom,
              ),
            ),
          ),
          // 6. Bottom overlay: tool activity + transcript + controls.
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: SizedBox(
                width: double.infinity,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                  if (state.tools.isNotEmpty)
                    ToolActivityView(
                      tools: state.tools,
                      onPermission: notifier.respondToolPermission,
                    ),
                  _TranscriptOverlay(entries: state.transcripts),
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
            ),
          ),
        ],
      ),
            ),
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
            RadioGroup<CaptureDeviceKind>(
              groupValue: _kindFromName(state.deviceKind),
              onChanged: (value) {
                if (value != null) notifier.switchDevice(value);
                Navigator.pop(context);
              },
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final kind in CaptureDeviceKind.values)
                    RadioListTile<CaptureDeviceKind>(
                      value: kind,
                      title: Text(_deviceLabel(kind)),
                      subtitle: kind == CaptureDeviceKind.glasses
                          ? const Text('Stub — BLE/RTSP transport TODO')
                          : null,
                    ),
                ],
              ),
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
    final wsKeyCtl =
        TextEditingController(text: current.webSearchApiKey ?? '');
    final wsFbKeyCtl =
        TextEditingController(text: current.webSearchFallbackApiKey ?? '');
    var secure = current.secure;
    var provider = current.provider;
    var wsProvider = current.webSearchProvider;
    final wsFbProvider = current.webSearchFallbackProvider;

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
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.devices_other),
                title: const Text('Capture device'),
                subtitle: Text(
                  current.provider == 'glasses'
                      ? 'Smart glasses'
                      : 'Phone (camera + mic)',
                ),
                onTap: () {
                  Navigator.pop(context);
                  _showDeviceSheet(
                    ref.read(liveProvider.notifier),
                    ref.read(liveProvider),
                  );
                },
              ),
              const SizedBox(height: 8),
              Text('AI provider',
                  style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                children: [
                  for (final p in const [
                    ('Gemini', 'gemini'),
                    ('OpenAI', 'openai'),
                    ('Grok', 'grok'),
                    ('Mock', 'mock'),
                  ])
                    ChoiceChip(
                      label: Text(p.$1),
                      selected: provider == p.$2,
                      onSelected: (_) => setSheet(() => provider = p.$2),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              Text('Web search',
                  style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                children: [
                  for (final p in const [
                    ('Tavily', 'tavily'),
                    ('Serper', 'serper'),
                    ('SerpAPI', 'serpapi'),
                  ])
                    ChoiceChip(
                      label: Text(p.$1),
                      selected: wsProvider == p.$2,
                      onSelected: (_) => setSheet(() => wsProvider = p.$2),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              TextField(
                controller: wsKeyCtl,
                obscureText: true,
                decoration: InputDecoration(
                  labelText: '$wsProvider API key',
                  hintText: 'leave blank to use server default',
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: wsFbKeyCtl,
                obscureText: true,
                decoration: InputDecoration(
                  labelText: 'Fallback ($wsFbProvider) key — optional',
                ),
              ),
              const SizedBox(height: 12),
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
                      provider: provider,
                      webSearchProvider: wsProvider,
                      webSearchApiKey: wsKeyCtl.text.trim(),
                      webSearchFallbackProvider: wsFbProvider,
                      webSearchFallbackApiKey: wsFbKeyCtl.text.trim(),
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
    final speaking = state.liveState == LiveState.speaking;

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.42),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _CircleButton(
                icon: state.cameraOn ? Icons.videocam : Icons.videocam_off,
                tooltip: state.cameraOn ? 'Turn camera off' : 'Turn camera on',
                onPressed: onToggleCamera,
              ),
              _MicButton(
                micOpen: state.micOpen,
                enabled: state.permissionsGranted,
                onPressed: onMicToggle,
              ),
              _CircleButton(
                icon: Icons.stop,
                tooltip: 'Interrupt',
                onPressed: speaking ? onInterrupt : null,
                danger: speaking,
              ),
            ],
          ),
          const SizedBox(height: 16),
          TextField(
            controller: textController,
            textInputAction: TextInputAction.send,
            style: const TextStyle(color: Aurora.textPrimary),
            onSubmitted: (text) {
              if (text.trim().isNotEmpty) onSendText(text);
            },
            decoration: InputDecoration(
              hintText: 'Type a message…',
              hintStyle: const TextStyle(color: Aurora.textMuted),
              isDense: true,
              filled: true,
              fillColor: Aurora.glass,
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: const BorderSide(color: Aurora.glassBorder),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: const BorderSide(color: Aurora.teal),
              ),
              suffixIcon: IconButton(
                icon: const Icon(Icons.send, color: Aurora.mint),
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

/// A glass circular icon button used for the secondary controls (camera,
/// interrupt). Turns red when [danger] is set (active barge-in).
class _CircleButton extends StatelessWidget {
  const _CircleButton({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.danger = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null;
    final fg = danger
        ? Aurora.danger
        : (enabled ? Aurora.textPrimary : Aurora.textMuted);
    return Tooltip(
      message: tooltip,
      child: Material(
        color: danger ? Aurora.tint(Aurora.danger, 0.16) : Aurora.glass,
        shape: const CircleBorder(
          side: BorderSide(color: Aurora.glassBorder),
        ),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onPressed,
          child: SizedBox(
            width: 52,
            height: 52,
            child: Icon(icon, size: 22, color: fg),
          ),
        ),
      ),
    );
  }
}

/// The big circular push-to-talk mic: teal at rest, red while listening.
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
    // Hands-free: the mic is open by default (teal = actively listening); tap
    // to mute (red, mic-off). No more press-every-time.
    final fill = !enabled
        ? Aurora.surfaceHigh
        : (micOpen ? Aurora.teal : Aurora.danger);
    final fg = micOpen ? Aurora.tealInk : Colors.white;
    return Tooltip(
      message: micOpen ? 'Listening — tap to mute' : 'Muted — tap to listen',
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: fill,
          boxShadow: enabled
              ? [
                  BoxShadow(
                    color: (micOpen ? Aurora.danger : Aurora.teal)
                        .withValues(alpha: 0.22),
                    blurRadius: 0,
                    spreadRadius: 6,
                  ),
                ]
              : null,
        ),
        child: Material(
          color: Colors.transparent,
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: enabled ? onPressed : null,
            child: SizedBox(
              width: 76,
              height: 76,
              child: Icon(
                micOpen ? Icons.mic : Icons.mic_off,
                size: 32,
                color: enabled ? fg : Aurora.textMuted,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Top overlay bar: status pills, the zoom read-out, and quick actions, on a
/// dark translucent strip so it stays legible over the live camera.
class _TopOverlay extends StatelessWidget {
  const _TopOverlay({
    required this.state,
    required this.onSettings,
    required this.onOrientation,
    required this.onNotes,
  });

  final LiveSessionState state;
  final VoidCallback onSettings;
  final VoidCallback onOrientation;
  final VoidCallback onNotes;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(12, 10, 4, 10),
      color: Colors.black.withValues(alpha: 0.34),
      child: Row(
        children: [
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  if (state.cameraOn) ...[
                    const _LiveBadge(),
                    const SizedBox(width: 8),
                  ],
                  StatusIndicator(
                    connection: state.connection,
                    liveState: state.liveState,
                    deviceKind: state.deviceKind,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(width: 8),
          _ZoomReadout(zoom: state.cameraZoom),
          IconButton(
            tooltip: 'Notes & tasks',
            icon: const Icon(Icons.checklist, color: Colors.white),
            onPressed: onNotes,
          ),
          IconButton(
            tooltip: state.cameraPortrait
                ? 'Switch to landscape'
                : 'Switch to portrait',
            icon: Icon(
              state.cameraPortrait
                  ? Icons.screen_rotation
                  : Icons.screen_lock_rotation,
              color: Colors.white,
            ),
            onPressed: onOrientation,
          ),
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.settings, color: Colors.white),
            onPressed: onSettings,
          ),
        ],
      ),
    );
  }
}

/// Pill showing the current zoom magnification (e.g. "2.0×").
class _ZoomReadout extends StatelessWidget {
  const _ZoomReadout({required this.zoom});

  final double zoom;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withValues(alpha: 0.22)),
      ),
      child: Text(
        '${zoom.toStringAsFixed(1)}×',
        style: const TextStyle(
          color: Colors.white,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Vertical zoom preset chips (4× / 2× / 1×) on the camera's right edge.
class _ZoomPresets extends StatelessWidget {
  const _ZoomPresets({required this.current, required this.onPick});

  final double current;
  final ValueChanged<double> onPick;

  @override
  Widget build(BuildContext context) {
    const presets = [4.0, 2.0, 1.0];
    return Padding(
      padding: const EdgeInsets.only(right: 10),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final p in presets)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: _ZoomChip(
                label: '${p.toStringAsFixed(0)}×',
                selected: (current - p).abs() < 0.25,
                onTap: () => onPick(p),
              ),
            ),
        ],
      ),
    );
  }
}

class _ZoomChip extends StatelessWidget {
  const _ZoomChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 42,
        height: 30,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: selected ? Aurora.teal : Colors.white.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? Aurora.tealInk : Colors.white,
            fontSize: 11,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}

/// Recent transcript lines as a constrained, translucent overlay above the
/// controls (hidden until there is something to show).
class _TranscriptOverlay extends StatelessWidget {
  const _TranscriptOverlay({required this.entries});

  final List<TranscriptEntry> entries;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.28,
      ),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.38),
        borderRadius: BorderRadius.circular(16),
      ),
      child: TranscriptView(entries: entries),
    );
  }
}

/// Small "LIVE" badge shown over the camera hero while streaming.
class _LiveBadge extends StatelessWidget {
  const _LiveBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 8, color: Aurora.danger),
          SizedBox(width: 5),
          Text(
            'LIVE',
            style: TextStyle(
              color: Aurora.textPrimary,
              fontSize: 10,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.5,
            ),
          ),
        ],
      ),
    );
  }
}
