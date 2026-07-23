import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/finder_api.dart';
import '../../data/live_client.dart';
import '../../protocol/protocol.dart';
import '../../state/auth.dart';
import '../../state/live_state.dart';
import '../../state/permissions.dart';
import '../../state/providers.dart';
import '../data/your_stuff_screen.dart';
import '../finder/finder_result_view.dart';
import '../finder/finder_screen.dart';
import '../glasses_lab/glasses_lab_screen.dart';
import '../settings/settings_screen.dart';
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
  StreamSubscription<FinderDetection>? _finderSub;
  bool _finderSheetOpen = false;
  // Live transcript is hidden by default (clean camera view); the chat toggle
  // in the top bar reveals it. Recording/saving is unaffected — it happens on
  // session end regardless of whether the transcript is on screen.
  bool _showChat = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Kick off connection after first frame so providers are ready.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_connect());
      // Voice flow (#3): when identify_image returns, show the same result sheet
      // the scan button shows so the user can tap Maps/Wikipedia/shop links.
      // Only while the app is actually VISIBLE: presenting the sheet pauses
      // the live mic, and with the screen off the sheet can never be
      // dismissed — the session stayed mute until the user came back
      // (device-proven 2026-07-11). Screen-off users get the spoken answer;
      // the sheet is a bonus for when they're looking.
      _finderSub = ref.read(liveControllerProvider).finderEvents.listen((d) {
        final visible = WidgetsBinding.instance.lifecycleState ==
            AppLifecycleState.resumed;
        if (mounted && visible) _presentFinder(detection: d);
      });
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _finderSub?.cancel();
    _textController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState lifecycle) {
    // Recover the camera across background/foreground: Android invalidates the
    // camera when the app is backgrounded, which froze the preview until the
    // app was force-closed. Release it on pause, re-open it on resume.
    final controller = ref.read(liveControllerProvider);
    if (lifecycle == AppLifecycleState.paused) {
      unawaited(controller.handleAppBackground());
    } else if (lifecycle == AppLifecycleState.resumed) {
      unawaited(controller.handleAppForeground());
    }
  }

  /// Flow #2: identify whatever the live camera currently sees.
  Future<void> _scanCurrentView() async {
    final controller = ref.read(liveControllerProvider);
    final frame = await controller.grabFrame();
    if (!mounted) return;
    if (frame == null) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(const SnackBar(
          content: Text('No camera frame yet — turn the camera on and try again.'),
        ));
      return;
    }
    await _presentFinder(
      future: ref.read(finderApiProvider).detect(imageBytes: frame),
    );
  }

  /// Present the detection in a draggable bottom sheet. Pass [detection] when
  /// it's already resolved (voice), or [future] to show a loading state first
  /// (scan button).
  Future<void> _presentFinder({
    FinderDetection? detection,
    Future<FinderDetection>? future,
  }) async {
    if (_finderSheetOpen) return;
    _finderSheetOpen = true;
    // Pause the live mic while the Finder result is open so the assistant
    // doesn't keep listening/replying in the background while the user reads.
    final controller = ref.read(liveControllerProvider);
    final wasListening = ref.read(liveProvider).micOpen;
    if (wasListening) await controller.stopListening();
    try {
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Aurora.surface,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        builder: (_) => DraggableScrollableSheet(
          expand: false,
          initialChildSize: 0.55,
          minChildSize: 0.3,
          maxChildSize: 0.92,
          builder: (_, scrollController) => _FinderSheet(
            detection: detection,
            future: future,
            scrollController: scrollController,
          ),
        ),
      );
    } finally {
      // Always release the guard, even if the sheet failed to present —
      // otherwise scan + voice sheets would be permanently disabled.
      _finderSheetOpen = false;
      // Resume listening if we paused it (and the session is still up).
      if (wasListening && mounted) await controller.startListening();
    }
  }

  Future<void> _connect() async {
    if (_connectRequested) return;
    _connectRequested = true;
    final outcome = await ref.read(liveProvider.notifier).connect();
    if (outcome != PermissionOutcome.granted && mounted) {
      _showPermissionDialog(outcome);
    }
  }

  Future<void> _startUpgrade(BuildContext context, LiveNotifier notifier) async {
    // Default the upgrade to Plus — the cheapest paid tier that lifts the cap.
    // A plan picker can come later; the point at the cap is to get them moving.
    final problem = await notifier.startUpgrade('plus');
    if (problem != null && context.mounted) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(problem)));
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
              ? 'Farry needs the microphone and camera. Please enable them in '
                  'Settings to use voice and vision.'
              : 'Farry needs the microphone and camera to see and hear. '
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
                chatOn: _showChat,
                onToggleChat: () => setState(() => _showChat = !_showChat),
                onFinder: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => const FinderScreen(),
                  ),
                ),
                onNotes: () => YourStuffScreen.open(context),
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
          // 5b. Compact status row below the top bar — mic-device chip and a
          //     small glasses pill (icon + battery), side by side so they
          //     never overlap each other or the transcript.
          SafeArea(
            child: Align(
              alignment: Alignment.topCenter,
              child: Padding(
                padding: const EdgeInsets.only(top: 58),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _MicChip(state: state),
                    const SizedBox(width: 8),
                    _CamChip(state: state),
                    if (state.audioKind == 'glasses' || state.glassesConnected)
                      ...[
                      const SizedBox(width: 8),
                      _GlassesPill(state: state),
                    ],
                  ],
                ),
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
                  // The result card + transcript can grow taller than the space
                  // left when the keyboard is open (a rich product/landmark card
                  // plus the typed-message field). Wrap them in a bottom-anchored
                  // scroll view so they scroll instead of overflowing ("BOTTOM
                  // OVERFLOWED BY … PIXELS"); the controls stay pinned below.
                  Flexible(
                    child: SingleChildScrollView(
                      reverse: true,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (state.tools.isNotEmpty)
                            ToolActivityView(
                              tools: state.tools,
                              onPermission: notifier.respondToolPermission,
                            ),
                          // Chat is hidden until the user taps the chat toggle
                          // (recording continues regardless).
                          if (_showChat)
                            _TranscriptOverlay(entries: state.transcripts),
                          // Just above the controls (never over the header): the
                          // last glasses photo, so the user sees exactly what
                          // was captured.
                          if (state.lastCapturedPhoto != null)
                            _CapturedPhotoPreview(
                              photo: state.lastCapturedPhoto!,
                              at: state.lastCapturedAt,
                              label: state.videoKind == 'glasses'
                                  ? 'Glasses captured'
                                  : 'Image sent to AI',
                            ),
                        ],
                      ),
                    ),
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
                    onFlipCamera: () =>
                        notifier.setCameraFront(!state.cameraFront),
                    onScan: _scanCurrentView,
                    onCapturePhoto: notifier.captureGlassesPhoto,
                  ),
                  ],
                ),
              ),
            ),
          ),
          // 7. Reconnect overlay — shown after the session ends or drops.
          if (state.connection == ConnectionStatus.disconnected)
            Positioned.fill(
              child: ReconnectOverlay(
                onReconnect: notifier.connect,
                capReached: state.capReached,
                onUpgrade: () => _startUpgrade(context, notifier),
              ),
            ),
        ],
      ),
            ),
          ),
        ),
    );
  }

  /// Open the redesigned, full-screen Settings hub. Every option inside writes
  /// to the same [configProvider] / [liveProvider] as before — only the layout
  /// changed. The glasses-lab teardown/reopen dance stays here (it must tear the
  /// session down first) and is handed to the hub as a callback.
  void _showSettingsSheet() {
    SettingsScreen.open(
      context,
      onOpenGlassesLab: () async {
        // The Lab owns the mic + Bluetooth while open: tear the live session
        // down first so FarryOn doesn't keep listening/answering mid-hardware-
        // test, then restore it when the Lab closes. Timeout guard: a session
        // stuck in backend connect-retry never resolves disconnect() and must
        // not block the Lab from opening (hit on-device 2026-07-06).
        await ref
            .read(liveProvider.notifier)
            .disconnect()
            .timeout(const Duration(seconds: 2), onTimeout: () {});
        if (!mounted) return;
        await GlassesLabScreen.open(context);
        if (!mounted) return;
        _connectRequested = false;
        await _connect();
      },
    );
  }
}

/// Always-visible chip telling the user which device the mic is using
/// (Phone/earbuds vs Glasses) and whether it's actively listening.
class _MicChip extends StatelessWidget {
  const _MicChip({required this.state});

  final LiveSessionState state;

  @override
  Widget build(BuildContext context) {
    final glasses = state.audioKind == 'glasses';
    final listening = state.micOpen;
    final color = listening ? Aurora.mint : Aurora.textMuted;
    final label = glasses ? 'Glasses mic' : 'Phone / earbuds';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(listening ? Icons.mic : Icons.mic_none, size: 14, color: color),
          const SizedBox(width: 6),
          Text(label,
              style: TextStyle(
                  color: color, fontSize: 12, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

/// Icon-only indicator of the active CAMERA — glasses eye when the glasses cam
/// is selected, phone-camera icon otherwise. Mirrors the mic chip so the user
/// can see at a glance which camera a photo will come from.
class _CamChip extends StatelessWidget {
  const _CamChip({required this.state});

  final LiveSessionState state;

  @override
  Widget build(BuildContext context) {
    final glasses = state.videoKind == 'glasses';
    final color = state.cameraOn ? Aurora.mint : Aurora.textMuted;
    return Container(
      padding: const EdgeInsets.all(7),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Icon(
        glasses ? Icons.visibility : Icons.photo_camera,
        size: 14,
        color: color,
      ),
    );
  }
}

/// Compact glasses status pill — sits side-by-side with the mic chip. Shows
/// ONLY a bluetooth-connection icon and the battery %, no prose (the user
/// asked for a clean, professional indicator, not a sentence).
class _GlassesPill extends StatelessWidget {
  const _GlassesPill({required this.state});

  final LiveSessionState state;

  @override
  Widget build(BuildContext context) {
    final connected = state.glassesConnected;
    final battery = state.glassesBattery;
    final low = battery != null && battery <= 20;
    // Amber while connecting, red on low battery, teal when healthy.
    final color = !connected
        ? Aurora.amber
        : low
            ? Aurora.danger
            : Aurora.teal;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            connected
                ? Icons.bluetooth_connected
                : Icons.bluetooth_searching,
            size: 14,
            color: color,
          ),
          if (connected && battery != null) ...[
            const SizedBox(width: 6),
            Icon(low ? Icons.battery_alert : Icons.battery_full,
                size: 13, color: color),
            const SizedBox(width: 2),
            Text('$battery%',
                style: TextStyle(
                    color: color,
                    fontSize: 12,
                    fontWeight: low ? FontWeight.w700 : FontWeight.w600)),
          ],
        ],
      ),
    );
  }
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
    required this.onFlipCamera,
    required this.onScan,
    required this.onCapturePhoto,
  });

  final LiveSessionState state;
  final TextEditingController textController;
  final VoidCallback onMicToggle;
  final VoidCallback onInterrupt;
  final ValueChanged<String> onSendText;
  final VoidCallback onToggleCamera;
  final VoidCallback onFlipCamera;
  final VoidCallback onScan;
  final VoidCallback onCapturePhoto;

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
                gradient: Aurora.gradBlue,
                onPressed: onToggleCamera,
              ),
              // Front/back lens flip — only for the phone camera (glasses have a
              // single fixed lens). Enabled while the camera is on.
              if (state.videoKind != 'glasses')
                _CircleButton(
                  icon: Icons.flip_camera_ios,
                  tooltip: state.cameraFront
                      ? 'Switch to back camera'
                      : 'Switch to front camera',
                  gradient: Aurora.gradTeal,
                  onPressed: state.cameraOn ? onFlipCamera : null,
                ),
              // B3: glasses shutter — take a still through the glasses camera
              // and let Farry look at it. Only shown when the glasses are the
              // vision source (the phone camera streams continuously, so it
              // doesn't need a shutter).
              if (state.videoKind == 'glasses')
                _CircleButton(
                  icon: Icons.photo_camera,
                  tooltip: 'Take a photo through the glasses',
                  gradient: Aurora.gradGreen,
                  onPressed: state.glassesConnected ? onCapturePhoto : null,
                ),
              _CircleButton(
                icon: Icons.center_focus_strong,
                tooltip: 'Identify what the camera sees',
                gradient: Aurora.gradPurple,
                onPressed: state.cameraOn ? onScan : null,
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
                icon: const GradientIcon(Icons.send,
                    gradient: Aurora.gradTeal, size: 22),
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
    this.gradient,
    this.danger = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;

  /// Colourful fill for the glyph when the button is active. Skipped while
  /// disabled (muted grey) so the disabled state stays obvious.
  final Gradient? gradient;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null;
    // Coral gradient for the active interrupt; the button's own gradient when
    // enabled; a flat muted glyph while disabled.
    final Widget glyph = danger
        ? GradientIcon(icon, gradient: Aurora.gradCoral, size: 22)
        : (enabled && gradient != null
            ? GradientIcon(icon, gradient: gradient!, size: 22)
            : Icon(icon,
                size: 22,
                color: enabled ? Aurora.textPrimary : Aurora.textMuted));
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
          child: SizedBox(width: 52, height: 52, child: glyph),
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
    // Listening: teal→mint gradient fill (primary action). Muted: solid danger.
    // Disabled: flat raised surface.
    final open = enabled && micOpen;
    final fill = !enabled ? Aurora.surfaceHigh : Aurora.danger;
    final fg = micOpen ? Aurora.tealInk : Colors.white;
    return Tooltip(
      message: micOpen ? 'Listening — tap to mute' : 'Muted — tap to listen',
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: open ? Aurora.primaryGradient : null,
          color: open ? null : fill,
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

/// Full-screen overlay shown when the session has ended/dropped, with a button
/// to start a new live session (voice can't restart it — the mic is off).
class ReconnectOverlay extends StatelessWidget {
  const ReconnectOverlay({
    super.key,
    required this.onReconnect,
    this.capReached = false,
    this.onUpgrade,
  });

  final VoidCallback onReconnect;

  /// The session ended because today's plan cap was spent. When true the
  /// overlay leads with Upgrade — a plain "Start session" would just re-hit the
  /// same cap and end again — and explains why, rather than the bare "Session
  /// ended" that reads like a fault.
  final bool capReached;
  final VoidCallback? onUpgrade;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Colors.black.withValues(alpha: 0.72),
      alignment: Alignment.center,
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            capReached ? Icons.hourglass_bottom_rounded : Icons.power_settings_new,
            size: 48,
            color: capReached ? Aurora.amber : Aurora.textMuted,
          ),
          const SizedBox(height: 12),
          Text(
            capReached ? "That's today's free minutes" : 'Session ended',
            style: const TextStyle(color: Aurora.textPrimary, fontSize: 18),
            textAlign: TextAlign.center,
          ),
          if (capReached) ...[
            const SizedBox(height: 8),
            const Text(
              'Upgrade for more voice time each day, or come back tomorrow.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 14),
              textAlign: TextAlign.center,
            ),
          ],
          const SizedBox(height: 18),
          if (capReached && onUpgrade != null)
            FilledButton.icon(
              onPressed: onUpgrade,
              icon: const Icon(Icons.workspace_premium_rounded),
              label: const Text('Upgrade'),
              style: FilledButton.styleFrom(
                backgroundColor: Aurora.amber,
                foregroundColor: Colors.black,
                padding:
                    const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
              ),
            ),
          if (capReached) const SizedBox(height: 10),
          // Start session stays available even at the cap: a new day may have
          // ticked over, so it's a secondary action rather than gone.
          capReached
              ? TextButton(
                  onPressed: onReconnect,
                  child: const Text('Try starting again',
                      style: TextStyle(color: Aurora.textMuted)),
                )
              : FilledButton.icon(
                  onPressed: onReconnect,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Start session'),
                  style: FilledButton.styleFrom(
                    backgroundColor: Aurora.teal,
                    foregroundColor: Aurora.tealInk,
                    padding: const EdgeInsets.symmetric(
                        horizontal: 24, vertical: 14),
                  ),
                ),
        ],
      ),
    );
  }
}

/// Top overlay bar: the connection status on the left, and a collapsible
/// cluster of quick actions on the right — tap the round toggle to reveal
/// Chat / Finder / Your stuff / Settings, tap again to tuck them away and keep
/// the camera view clean.
class _TopOverlay extends StatefulWidget {
  const _TopOverlay({
    required this.state,
    required this.onSettings,
    required this.onFinder,
    required this.onNotes,
    required this.chatOn,
    required this.onToggleChat,
  });

  final LiveSessionState state;
  final VoidCallback onSettings;
  final VoidCallback onFinder;
  final VoidCallback onNotes;

  /// Whether the live transcript is currently shown (drives the chat icon).
  final bool chatOn;
  final VoidCallback onToggleChat;

  @override
  State<_TopOverlay> createState() => _TopOverlayState();
}

class _TopOverlayState extends State<_TopOverlay> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final state = widget.state;
    // A floating rounded bar (with a margin from the camera's rounded corners)
    // so nothing gets clipped by the 24px viewport radius and it reads as a
    // clean, intentional control bar.
    return Container(
      margin: const EdgeInsets.fromLTRB(8, 6, 8, 0),
      padding: const EdgeInsets.fromLTRB(12, 6, 6, 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.42),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        children: [
          // Left cluster shrinks (scaleDown) instead of overflowing when the
          // connection pill is wide ("Reconnecting") on narrow screens.
          Flexible(
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (state.cameraOn) ...[
                    const _LiveBadge(),
                    const SizedBox(width: 8),
                  ],
                  StatusIndicator(
                    connection: state.connection,
                    liveState: state.liveState,
                    deviceKind: state.deviceKind,
                    connectionOnly: true,
                  ),
                ],
              ),
            ),
          ),
          const Spacer(),
          // Outside the AnimatedSize on purpose: the account avatar must be
          // visible at rest, not hidden behind the "more actions" toggle.
          _AccountAvatar(onTap: widget.onSettings),
          // Collapsible actions: only the toggle shows at rest; it slides the
          // rest open on tap.
          AnimatedSize(
            duration: const Duration(milliseconds: 220),
            curve: Curves.easeOut,
            alignment: Alignment.centerRight,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (_open) ...[
                  _BarIcon(
                    widget.chatOn
                        ? Icons.chat_rounded
                        : Icons.chat_bubble_outline_rounded,
                    widget.chatOn ? 'Hide chat' : 'Show chat',
                    widget.onToggleChat,
                    gradient: Aurora.gradAmber,
                  ),
                  _BarIcon(Icons.image_search_rounded,
                      'Finder — identify a photo', widget.onFinder,
                      gradient: Aurora.gradBlue),
                  _BarIcon(Icons.grid_view_rounded, 'Your stuff', widget.onNotes,
                      gradient: Aurora.gradGreen),
                  _BarIcon(Icons.settings_rounded, 'Settings', widget.onSettings,
                      gradient: Aurora.gradPurple),
                ],
                _BarIcon(
                  _open ? Icons.close_rounded : Icons.more_horiz_rounded,
                  _open ? 'Close menu' : 'More actions',
                  () => setState(() => _open = !_open),
                  gradient: Aurora.gradTeal,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// First letter of the name, else of the email, else a neutral dot — never a
/// blank circle, and never "?" (which reads as an error, not as a person).
///
/// Takes a whole grapheme rather than `source[0]`, so a non-Latin or emoji name
/// isn't sliced mid-character into a replacement glyph.
String accountInitialFor(String? displayName, String email) {
  for (final source in [displayName ?? '', email]) {
    final trimmed = source.trim();
    if (trimmed.isNotEmpty) return trimmed.characters.first.toUpperCase();
  }
  return '•';
}

/// The signed-in person, as a tappable initial. Sits outside the collapsible
/// cluster because it answers a question the user has *before* they go looking
/// for anything: am I signed in, and as whom? Until this existed the only proof
/// was buried two taps deep in Settings, so a successful Google sign-in looked
/// exactly like no sign-in at all.
///
/// Tapping opens Settings, where the full name, email and Sign out live — hence
/// the same pink as that screen's Account row, so it reads as the same thing.
class _AccountAvatar extends ConsumerWidget {
  const _AccountAvatar({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider);
    // Signed out can't happen on this screen (AuthGate swaps it for the splash)
    // and restoring is a blink — drawing an empty circle for either would be a
    // worse lie than drawing nothing.
    if (!auth.isSignedIn) return const SizedBox.shrink();

    return Tooltip(
      message: auth.email.isNotEmpty ? 'Signed in as ${auth.email}' : 'Account',
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: Padding(
          padding: const EdgeInsets.all(5),
          child: Container(
            width: 28,
            height: 28,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: Aurora.gradPink,
            ),
            child: Text(
              accountInitialFor(auth.displayName, auth.email),
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w800,
                color: Colors.white,
                height: 1.0,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Compact icon button for the floating top bar (so all icons fit even when the
/// connection-status pill is wide, e.g. "Connecting").
class _BarIcon extends StatelessWidget {
  const _BarIcon(this.icon, this.tooltip, this.onTap, {this.gradient});
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;
  final Gradient? gradient;

  @override
  Widget build(BuildContext context) => IconButton(
        tooltip: tooltip,
        icon: gradient != null
            ? GradientIcon(icon, gradient: gradient!, size: 22)
            : Icon(icon, color: Colors.white),
        onPressed: onTap,
        iconSize: 22,
        visualDensity: VisualDensity.compact,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(minWidth: 38, minHeight: 38),
      );
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
/// Shows the most recent glasses photo (what was actually captured and sent
/// for recognition) so the user can visually confirm it matches where the
/// glasses point. Tap to view full-screen. This is the ground-truth check for
/// "it described the wrong scene".
class _CapturedPhotoPreview extends StatelessWidget {
  const _CapturedPhotoPreview({
    required this.photo,
    required this.at,
    this.label = 'Glasses captured',
  });

  final Uint8List photo;
  final DateTime? at;

  /// Caption shown above the timestamp — names what this frame is (e.g.
  /// "Glasses captured" vs the phone camera view sent to the AI).
  final String label;

  String _stamp(DateTime t) {
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      child: Align(
        alignment: Alignment.centerLeft,
        child: GestureDetector(
          onTap: () => showDialog<void>(
            context: context,
            builder: (_) => Dialog(
              backgroundColor: Colors.transparent,
              insetPadding: const EdgeInsets.all(12),
              child: InteractiveViewer(
                child: Image.memory(photo, fit: BoxFit.contain),
              ),
            ),
          ),
          child: Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.55),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(9),
                  child: Image.memory(
                    photo,
                    width: 88,
                    height: 66,
                    fit: BoxFit.cover,
                    gaplessPlayback: true, // don't flash between captures
                  ),
                ),
                const SizedBox(width: 10),
                Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.92),
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      at == null ? 'tap to enlarge' : '${_stamp(at!)} · tap to enlarge',
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.6),
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
                const SizedBox(width: 8),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TranscriptOverlay extends StatelessWidget {
  const _TranscriptOverlay({required this.entries});

  final List<TranscriptEntry> entries;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      constraints: BoxConstraints(
        // Cap so the transcript yields room for the tool card, photo preview,
        // and controls below it — otherwise the bottom column overflows (the
        // yellow "BOTTOM OVERFLOWED" stripe) when all are shown at once.
        maxHeight: (MediaQuery.of(context).size.height * 0.5)
            .clamp(120.0, MediaQuery.of(context).size.height - 440),
      ),
      decoration: BoxDecoration(
        // A dark vertical gradient (no live blur — keeps it cheap) gives the
        // text a readable backdrop over the bright camera feed.
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Colors.black.withValues(alpha: 0.26),
            Colors.black.withValues(alpha: 0.58),
          ],
        ),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
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

/// Bottom-sheet body for a Finder result — either a resolved [detection]
/// (voice) or a [future] that shows a loading state first (scan button).
/// A radar-style "scanning" pulse shown while the Finder identifies an image —
/// expanding teal rings around a glowing viewfinder, with a meaningful caption.
class _IdentifyingAnimation extends StatefulWidget {
  const _IdentifyingAnimation();

  @override
  State<_IdentifyingAnimation> createState() => _IdentifyingAnimationState();
}

class _IdentifyingAnimationState extends State<_IdentifyingAnimation>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1800),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Column(
        children: [
          SizedBox(
            width: 132,
            height: 132,
            child: AnimatedBuilder(
              animation: _c,
              builder: (context, _) => Stack(
                alignment: Alignment.center,
                children: [
                  for (var i = 0; i < 3; i++)
                    _ring((_c.value + i / 3) % 1.0),
                  Container(
                    width: 60,
                    height: 60,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      gradient: const RadialGradient(
                        colors: [Aurora.mint, Aurora.teal],
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: Aurora.teal.withValues(alpha: 0.55),
                          blurRadius: 18,
                          spreadRadius: 1,
                        ),
                      ],
                    ),
                    child: const Icon(Icons.center_focus_strong,
                        color: Colors.white, size: 30),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          const Text('Identifying…',
              style: TextStyle(
                color: Aurora.mint,
                fontSize: 17,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.3,
              )),
          const SizedBox(height: 6),
          const Text('Looking closely at what you captured',
              style: TextStyle(color: Aurora.textMuted, fontSize: 13)),
        ],
      ),
    );
  }

  Widget _ring(double t) {
    final size = 54 + t * 74; // expand outward
    return Opacity(
      opacity: (1 - t) * 0.5, // fade as it grows
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: Border.all(color: Aurora.teal, width: 2),
        ),
      ),
    );
  }
}

class _FinderSheet extends StatelessWidget {
  const _FinderSheet({
    required this.detection,
    required this.future,
    required this.scrollController,
  });

  final FinderDetection? detection;
  final Future<FinderDetection>? future;
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          margin: const EdgeInsets.only(top: 10, bottom: 4),
          width: 40,
          height: 4,
          decoration: BoxDecoration(
            color: Aurora.glassBorder,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        Expanded(
          child: SingleChildScrollView(
            controller: scrollController,
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            child: detection != null
                ? FinderResultView(detection!)
                : FutureBuilder<FinderDetection>(
                    future: future,
                    builder: (context, snap) {
                      if (snap.connectionState != ConnectionState.done) {
                        return const _IdentifyingAnimation();
                      }
                      if (snap.hasError) {
                        return FinderResultView(FinderDetection(
                          ok: false,
                          mode: 'error',
                          error: snap.error.toString(),
                        ));
                      }
                      return FinderResultView(snap.data!);
                    },
                  ),
          ),
        ),
      ],
    );
  }
}
