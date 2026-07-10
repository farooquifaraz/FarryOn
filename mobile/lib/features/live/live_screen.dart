import 'dart:async';

import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/theme.dart';
import '../../data/finder_api.dart';
import '../../data/live_client.dart';
import '../../protocol/protocol.dart';
import '../../state/live_state.dart';
import '../../state/permissions.dart';
import '../../state/providers.dart';
import '../data/notes_tasks_screen.dart';
import '../debug/debug_logs_screen.dart';
import '../finder/finder_result_view.dart';
import '../glasses_lab/glasses_lab_screen.dart';
import '../finder/finder_screen.dart';
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

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Kick off connection after first frame so providers are ready.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_connect());
      // Voice flow (#3): when identify_image returns, show the same result sheet
      // the scan button shows so the user can tap Maps/Wikipedia/shop links.
      // Present failures too (no frame / Vision error / nothing found) — the
      // sheet renders a friendly error state, otherwise the voice path would
      // show nothing at all on failure.
      _finderSub = ref.read(liveControllerProvider).finderEvents.listen((d) {
        if (mounted) _presentFinder(detection: d);
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
                onOrientation: () =>
                    notifier.setCameraPortrait(!state.cameraPortrait),
                onFinder: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => const FinderScreen(),
                  ),
                ),
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
              child: _ReconnectOverlay(onReconnect: notifier.connect),
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
              title: Text('Capture devices'),
              subtitle: Text(
                  'Mic and camera pick independently — e.g. earbuds mic + glasses camera'),
            ),
            // --- Microphone ---
            const ListTile(
              dense: true,
              leading: Icon(Icons.mic, color: Aurora.textMuted),
              title: Text('Microphone'),
            ),
            RadioGroup<CaptureDeviceKind>(
              groupValue: _kindFromName(state.audioKind),
              onChanged: (value) {
                if (value != null) notifier.setAudioDevice(value);
                Navigator.pop(context);
              },
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final kind in CaptureDeviceKind.values)
                    RadioListTile<CaptureDeviceKind>(
                      value: kind,
                      dense: true,
                      title: Text(kind == CaptureDeviceKind.phone
                          ? 'Phone / earbuds mic'
                          : 'Glasses mic (long-press to talk)'),
                    ),
                ],
              ),
            ),
            const Divider(height: 1),
            // --- Camera ---
            const ListTile(
              dense: true,
              leading: Icon(Icons.photo_camera, color: Aurora.textMuted),
              title: Text('Camera'),
            ),
            RadioGroup<CaptureDeviceKind>(
              groupValue: _kindFromName(state.videoKind),
              onChanged: (value) {
                if (value != null) notifier.setVideoDevice(value);
                Navigator.pop(context);
              },
              child: const Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  RadioListTile<CaptureDeviceKind>(
                    value: CaptureDeviceKind.phone,
                    dense: true,
                    title: Text('Phone camera (live 1 fps)'),
                  ),
                  RadioListTile<CaptureDeviceKind>(
                    value: CaptureDeviceKind.glasses,
                    dense: true,
                    enabled: false,
                    title: Text('Glasses camera (photo-trigger — coming in B3)'),
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
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Aurora.base,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetContext) => _SettingsSheet(
        current: ref.read(configProvider),
        onSave: (cfg) => ref.read(configProvider.notifier).state = cfg,
        onOpenDevices: () {
          Navigator.pop(sheetContext);
          _showDeviceSheet(
            ref.read(liveProvider.notifier),
            ref.read(liveProvider),
          );
        },
        onOpenGlassesLab: () async {
          // The Lab owns the mic + Bluetooth while open: tear the live
          // session down first so FarryOn doesn't keep listening/answering
          // mid-hardware-test, then restore it when the Lab closes.
          // Timeout guard: a session stuck in backend connect-retry never
          // resolves disconnect() and must not block the Lab from opening
          // (hit on-device 2026-07-06).
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
      ),
    );
  }

  static CaptureDeviceKind _kindFromName(String name) =>
      name == 'glasses' ? CaptureDeviceKind.glasses : CaptureDeviceKind.phone;
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

/// The live, cloud-hosted FarryOn backend (Render). One tap fills these in so
/// the user never has to know the host/port.
const String _kCloudHost = 'farryon-backend.onrender.com';
const int _kCloudPort = 443;

/// Default local/dev backend (the user's PC on the LAN). Editable below the
/// toggle, but one tap fills the common case.
const String _kLocalHost = '192.168.1.107';
const int _kLocalPort = 8000;

/// A scrollable, keyboard-safe settings sheet with a pinned Save bar. Replaces
/// the old fixed Column that pushed the Save button (and the lower fields) off
/// screen once the keyboard opened.
class _SettingsSheet extends StatefulWidget {
  const _SettingsSheet({
    required this.current,
    required this.onSave,
    required this.onOpenDevices,
    required this.onOpenGlassesLab,
  });

  final AppConfig current;
  final ValueChanged<AppConfig> onSave;
  final VoidCallback onOpenDevices;
  final VoidCallback onOpenGlassesLab;

  @override
  State<_SettingsSheet> createState() => _SettingsSheetState();
}

class _SettingsSheetState extends State<_SettingsSheet> {
  late final _hostCtl = TextEditingController(text: widget.current.host);
  late final _portCtl =
      TextEditingController(text: widget.current.port.toString());
  late final _wsKeyCtl =
      TextEditingController(text: widget.current.webSearchApiKey ?? '');
  late final _wsFbKeyCtl =
      TextEditingController(text: widget.current.webSearchFallbackApiKey ?? '');
  late final _emailCtl =
      TextEditingController(text: widget.current.emailAddress ?? '');
  late final _emailPwCtl =
      TextEditingController(text: widget.current.emailAppPassword ?? '');
  late final _imapCtl =
      TextEditingController(text: widget.current.emailImapHost ?? '');
  late final _smtpCtl =
      TextEditingController(text: widget.current.emailSmtpHost ?? '');
  late final _smtpPortCtl =
      TextEditingController(text: widget.current.emailSmtpPort.toString());

  late bool _secure = widget.current.secure;
  late String _provider = widget.current.provider;
  late String _wsProvider = widget.current.webSearchProvider;
  late String _emailProvider = widget.current.emailProvider;
  late bool _handsFree = widget.current.handsFree;
  bool _showEmailPw = false;

  @override
  void dispose() {
    _hostCtl.dispose();
    _portCtl.dispose();
    _wsKeyCtl.dispose();
    _wsFbKeyCtl.dispose();
    _emailCtl.dispose();
    _emailPwCtl.dispose();
    _imapCtl.dispose();
    _smtpCtl.dispose();
    _smtpPortCtl.dispose();
    super.dispose();
  }

  bool get _isCloud =>
      _hostCtl.text.trim() == _kCloudHost && _secure && _portCtl.text == '443';

  void _useCloud() {
    setState(() {
      _hostCtl.text = _kCloudHost;
      _portCtl.text = '$_kCloudPort';
      _secure = true;
    });
  }

  void _useLocal() {
    setState(() {
      _hostCtl.text = _kLocalHost;
      _portCtl.text = '$_kLocalPort';
      _secure = false;
    });
  }

  void _save() {
    final port = int.tryParse(_portCtl.text.trim()) ?? widget.current.port;
    // Resolve the mail hosts: presets fill them in; "custom" takes the fields.
    final preset = EmailProviders.presets[_emailProvider] ??
        EmailProviders.presets['gmail']!;
    final custom = _emailProvider == 'custom';
    final imapHost = custom ? _imapCtl.text.trim() : preset.imap;
    final smtpHost = custom ? _smtpCtl.text.trim() : preset.smtp;
    final smtpPort = custom
        ? (int.tryParse(_smtpPortCtl.text.trim()) ?? 587)
        : preset.port;
    widget.onSave(widget.current.copyWith(
      host: _hostCtl.text.trim(),
      port: port,
      secure: _secure,
      provider: _provider,
      webSearchProvider: _wsProvider,
      webSearchApiKey: _wsKeyCtl.text.trim(),
      webSearchFallbackApiKey: _wsFbKeyCtl.text.trim(),
      emailAddress: _emailCtl.text.trim(),
      emailAppPassword: _emailPwCtl.text.trim(),
      emailProvider: _emailProvider,
      emailImapHost: imapHost,
      emailSmtpHost: smtpHost,
      emailSmtpPort: smtpPort,
      handsFree: _handsFree,
    ));
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final maxH = MediaQuery.of(context).size.height * 0.9;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxH),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Drag handle.
            Container(
              margin: const EdgeInsets.only(top: 10, bottom: 4),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Aurora.glassBorder,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 8, 12, 4),
              child: Row(
                children: [
                  Text('Settings', style: theme.textTheme.titleLarge),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close, color: Aurora.textMuted),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _label('Connection'),
                    const SizedBox(height: 8),
                    _connectionStatus(),
                    const SizedBox(height: 12),
                    _modeToggle(),
                    const SizedBox(height: 14),
                    TextField(
                      controller: _hostCtl,
                      autocorrect: false,
                      onChanged: (_) => setState(() {}),
                      decoration: const InputDecoration(
                        labelText: 'Host',
                        hintText: 'farryon-backend.onrender.com',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        SizedBox(
                          width: 110,
                          child: TextField(
                            controller: _portCtl,
                            keyboardType: TextInputType.number,
                            onChanged: (_) => setState(() {}),
                            decoration: const InputDecoration(
                              labelText: 'Port',
                              border: OutlineInputBorder(),
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: SwitchListTile(
                            value: _secure,
                            title: const Text('Secure (TLS)'),
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            onChanged: (v) => setState(() => _secure = v),
                          ),
                        ),
                      ],
                    ),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.devices_other,
                          color: Aurora.textMuted),
                      title: const Text('Capture device'),
                      subtitle: Text(
                        widget.current.provider == 'glasses'
                            ? 'Smart glasses'
                            : 'Phone (camera + mic)',
                        style: const TextStyle(color: Aurora.textMuted),
                      ),
                      trailing: const Icon(Icons.chevron_right,
                          color: Aurora.textMuted),
                      onTap: widget.onOpenDevices,
                    ),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.bug_report_outlined,
                          color: Aurora.textMuted),
                      title: const Text('Debug logs'),
                      subtitle: const Text(
                        'View / share the tool + error trail to report issues',
                        style: TextStyle(color: Aurora.textMuted),
                      ),
                      trailing: const Icon(Icons.chevron_right,
                          color: Aurora.textMuted),
                      onTap: () => DebugLogsScreen.open(context),
                    ),
                    // Hardware test bench for the L801 smart glasses. Debug
                    // builds only — never visible in a release build.
                    if (kDebugMode)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.science_outlined,
                            color: Aurora.textMuted),
                        title: const Text('Glasses Lab'),
                        subtitle: const Text(
                          'L801 hardware test bench (debug builds only)',
                          style: TextStyle(color: Aurora.textMuted),
                        ),
                        trailing: const Icon(Icons.chevron_right,
                            color: Aurora.textMuted),
                        onTap: widget.onOpenGlassesLab,
                      ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      secondary: Icon(
                        _handsFree ? Icons.hearing : Icons.touch_app,
                        color: Aurora.textMuted,
                      ),
                      title: const Text('Hands-free mic'),
                      subtitle: Text(
                        _handsFree
                            ? 'Always listening (best in a quiet room)'
                            : 'Tap-to-talk: mic opens only when you tap it '
                                '(best with background noise / a TV)',
                        style: const TextStyle(color: Aurora.textMuted),
                      ),
                      value: _handsFree,
                      onChanged: (v) => setState(() => _handsFree = v),
                    ),
                    const Divider(height: 28, color: Aurora.glassBorder),
                    _label('AI provider'),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      children: [
                        // Grok (xAI) is intentionally omitted — its realtime
                        // API is ~13x slower and streams choppy audio. Gemini
                        // and OpenAI are the supported fast providers.
                        for (final p in const [
                          ('Gemini ⚡', 'gemini'),
                          ('OpenAI ⚡', 'openai'),
                          ('Mock', 'mock'),
                        ])
                          ChoiceChip(
                            label: Text(p.$1),
                            selected: _provider == p.$2,
                            onSelected: (_) =>
                                setState(() => _provider = p.$2),
                          ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Gemini & OpenAI are fast with smooth voice + vision. '
                      'Gemini is the best value (cheapest); OpenAI is premium.',
                      style: TextStyle(color: Aurora.textMuted, fontSize: 12),
                    ),
                    const Divider(height: 28, color: Aurora.glassBorder),
                    _label('Email — your own inbox (optional)'),
                    const SizedBox(height: 4),
                    Text(
                      _emailProvider == 'gmail'
                          ? 'Gmail: use a 16-digit App Password (not your login '
                              'password) from myaccount.google.com/apppasswords '
                              'after enabling 2-Step Verification.'
                          : 'Use your mailbox password (or an app password if '
                              'your provider requires one).',
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: Aurora.textMuted),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      children: [
                        for (final e in EmailProviders.presets.entries)
                          ChoiceChip(
                            label: Text(e.value.label),
                            selected: _emailProvider == e.key,
                            onSelected: (_) =>
                                setState(() => _emailProvider = e.key),
                          ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: _emailCtl,
                      keyboardType: TextInputType.emailAddress,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'Email address',
                        hintText: 'you@example.com — blank to disable',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: _emailPwCtl,
                      obscureText: !_showEmailPw,
                      autocorrect: false,
                      enableSuggestions: false,
                      decoration: InputDecoration(
                        labelText: 'App password',
                        border: const OutlineInputBorder(),
                        suffixIcon: IconButton(
                          icon: Icon(
                            _showEmailPw
                                ? Icons.visibility_off
                                : Icons.visibility,
                            color: Aurora.textMuted,
                          ),
                          onPressed: () =>
                              setState(() => _showEmailPw = !_showEmailPw),
                        ),
                      ),
                    ),
                    if (_emailProvider == 'custom') ...[
                      const SizedBox(height: 10),
                      TextField(
                        controller: _imapCtl,
                        autocorrect: false,
                        decoration: const InputDecoration(
                          labelText: 'IMAP host (incoming)',
                          hintText: 'mail.yourdomain.com',
                          border: OutlineInputBorder(),
                        ),
                      ),
                      const SizedBox(height: 10),
                      Row(
                        children: [
                          Expanded(
                            child: TextField(
                              controller: _smtpCtl,
                              autocorrect: false,
                              decoration: const InputDecoration(
                                labelText: 'SMTP host (outgoing)',
                                hintText: 'mail.yourdomain.com',
                                border: OutlineInputBorder(),
                              ),
                            ),
                          ),
                          const SizedBox(width: 10),
                          SizedBox(
                            width: 92,
                            child: TextField(
                              controller: _smtpPortCtl,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                labelText: 'Port',
                                hintText: '587',
                                border: OutlineInputBorder(),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ],
                    const Divider(height: 28, color: Aurora.glassBorder),
                    _label('Web search (optional)'),
                    const SizedBox(height: 8),
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
                            selected: _wsProvider == p.$2,
                            onSelected: (_) =>
                                setState(() => _wsProvider = p.$2),
                          ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: _wsKeyCtl,
                      obscureText: true,
                      decoration: InputDecoration(
                        labelText: '$_wsProvider API key',
                        hintText: 'blank = use server default',
                        border: const OutlineInputBorder(),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            // Pinned Save bar — always reachable, even with the keyboard open.
            Container(
              padding: EdgeInsets.fromLTRB(
                20,
                10,
                20,
                10 + MediaQuery.of(context).padding.bottom,
              ),
              decoration: const BoxDecoration(
                color: Aurora.surface,
                border: Border(top: BorderSide(color: Aurora.glassBorder)),
              ),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _save,
                  icon: const Icon(Icons.check),
                  label: const Text('Save & reconnect'),
                  style: FilledButton.styleFrom(
                    backgroundColor: Aurora.teal,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _label(String text) => Text(
        text.toUpperCase(),
        style: const TextStyle(
          color: Aurora.mint,
          fontSize: 12,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
        ),
      );

  /// Live connection status pill (watches the session) + the target it points at.
  Widget _connectionStatus() {
    return Consumer(
      builder: (context, ref, _) {
        final status = ref.watch(liveProvider.select((s) => s.connection));
        final (color, icon, label) = switch (status) {
          ConnectionStatus.connected => (Aurora.teal, Icons.check_circle, 'Connected'),
          ConnectionStatus.connecting => (Aurora.amber, Icons.sync, 'Connecting…'),
          ConnectionStatus.reconnecting =>
            (Aurora.amber, Icons.sync, 'Reconnecting…'),
          ConnectionStatus.disconnected => (Aurora.danger, Icons.error_outline, 'Offline'),
        };
        final scheme = _secure ? 'https' : 'http';
        final target = '$scheme://${_hostCtl.text.trim()}:${_portCtl.text.trim()}';
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          decoration: BoxDecoration(
            color: Aurora.tint(color, 0.14),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Aurora.tint(color, 0.3)),
          ),
          child: Row(
            children: [
              Icon(icon, color: color, size: 20),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(label,
                        style: TextStyle(
                            color: color, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 2),
                    Text(target,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: Aurora.textMuted, fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  /// Cloud vs Local (Dev) target picker — either can be used; one tap fills
  /// the host/port/TLS for that target (still editable below).
  Widget _modeToggle() {
    return Row(
      children: [
        Expanded(
          child: _modeChip(
            label: 'Cloud',
            subtitle: 'Always online',
            icon: Icons.cloud_outlined,
            selected: _isCloud,
            onTap: _useCloud,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _modeChip(
            label: 'Local (Dev)',
            subtitle: 'Your PC on Wi-Fi',
            icon: Icons.lan_outlined,
            selected: !_isCloud,
            onTap: _useLocal,
          ),
        ),
      ],
    );
  }

  Widget _modeChip({
    required String label,
    required String subtitle,
    required IconData icon,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        decoration: BoxDecoration(
          color: selected ? Aurora.teal.withValues(alpha: 0.18) : Aurora.glass,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: selected ? Aurora.teal : Aurora.glassBorder,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon,
                    size: 18,
                    color: selected ? Aurora.teal : Aurora.textMuted),
                const SizedBox(width: 8),
                Text(label,
                    style: TextStyle(
                        color: selected ? Aurora.textPrimary : Aurora.textMuted,
                        fontWeight: FontWeight.w600)),
                if (selected) ...[
                  const Spacer(),
                  const Icon(Icons.check_circle, color: Aurora.teal, size: 16),
                ],
              ],
            ),
            const SizedBox(height: 2),
            Text(subtitle,
                style: const TextStyle(color: Aurora.textMuted, fontSize: 11)),
          ],
        ),
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
    required this.onScan,
    required this.onCapturePhoto,
  });

  final LiveSessionState state;
  final TextEditingController textController;
  final VoidCallback onMicToggle;
  final VoidCallback onInterrupt;
  final ValueChanged<String> onSendText;
  final VoidCallback onToggleCamera;
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
                onPressed: onToggleCamera,
              ),
              // B3: glasses shutter — take a still through the glasses camera
              // and let Farry look at it. Only shown when the glasses are the
              // vision source (the phone camera streams continuously, so it
              // doesn't need a shutter).
              if (state.videoKind == 'glasses')
                _CircleButton(
                  icon: Icons.photo_camera,
                  tooltip: 'Take a photo through the glasses',
                  onPressed: state.glassesConnected ? onCapturePhoto : null,
                ),
              _CircleButton(
                icon: Icons.center_focus_strong,
                tooltip: 'Identify what the camera sees',
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

/// Full-screen overlay shown when the session has ended/dropped, with a button
/// to start a new live session (voice can't restart it — the mic is off).
class _ReconnectOverlay extends StatelessWidget {
  const _ReconnectOverlay({required this.onReconnect});

  final VoidCallback onReconnect;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Colors.black.withValues(alpha: 0.72),
      alignment: Alignment.center,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.power_settings_new,
              size: 48, color: Aurora.textMuted),
          const SizedBox(height: 12),
          const Text('Session ended',
              style: TextStyle(color: Aurora.textPrimary, fontSize: 18)),
          const SizedBox(height: 18),
          FilledButton.icon(
            onPressed: onReconnect,
            icon: const Icon(Icons.play_arrow),
            label: const Text('Start session'),
            style: FilledButton.styleFrom(
              backgroundColor: Aurora.teal,
              foregroundColor: Aurora.tealInk,
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
            ),
          ),
        ],
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
    required this.onFinder,
    required this.onNotes,
  });

  final LiveSessionState state;
  final VoidCallback onSettings;
  final VoidCallback onOrientation;
  final VoidCallback onFinder;
  final VoidCallback onNotes;

  @override
  Widget build(BuildContext context) {
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
          _BarIcon(Icons.image_search, 'Finder — identify a photo', onFinder),
          _BarIcon(Icons.checklist, 'Notes & tasks', onNotes),
          _BarIcon(
            state.cameraPortrait
                ? Icons.screen_rotation
                : Icons.screen_lock_rotation,
            state.cameraPortrait ? 'Switch to landscape' : 'Switch to portrait',
            onOrientation,
          ),
          _BarIcon(Icons.settings, 'Settings', onSettings),
        ],
      ),
    );
  }
}

/// Compact icon button for the floating top bar (so all icons fit even when the
/// connection-status pill is wide, e.g. "Connecting").
class _BarIcon extends StatelessWidget {
  const _BarIcon(this.icon, this.tooltip, this.onTap);
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => IconButton(
        tooltip: tooltip,
        icon: Icon(icon, color: Colors.white),
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
class _TranscriptOverlay extends StatelessWidget {
  const _TranscriptOverlay({required this.entries});

  final List<TranscriptEntry> entries;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.5,
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
