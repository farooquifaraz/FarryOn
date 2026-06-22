import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/theme.dart';
import '../../data/live_client.dart';
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

/// The live, cloud-hosted FarryOn backend (Render). One tap fills these in so
/// the user never has to know the host/port.
const String _kCloudHost = 'farryon-backend.onrender.com';
const int _kCloudPort = 443;

/// A scrollable, keyboard-safe settings sheet with a pinned Save bar. Replaces
/// the old fixed Column that pushed the Save button (and the lower fields) off
/// screen once the keyboard opened.
class _SettingsSheet extends StatefulWidget {
  const _SettingsSheet({
    required this.current,
    required this.onSave,
    required this.onOpenDevices,
  });

  final AppConfig current;
  final ValueChanged<AppConfig> onSave;
  final VoidCallback onOpenDevices;

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
                    _cloudButton(),
                    const SizedBox(height: 12),
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
                    const Divider(height: 28, color: Aurora.glassBorder),
                    _label('AI provider'),
                    const SizedBox(height: 8),
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
                            selected: _provider == p.$2,
                            onSelected: (_) =>
                                setState(() => _provider = p.$2),
                          ),
                      ],
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

  Widget _cloudButton() {
    final on = _isCloud;
    return InkWell(
      onTap: _useCloud,
      borderRadius: BorderRadius.circular(14),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: on ? Aurora.teal.withValues(alpha: 0.18) : Aurora.glass,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: on ? Aurora.teal : Aurora.glassBorder,
            width: on ? 1.5 : 1,
          ),
        ),
        child: Row(
          children: [
            Icon(Icons.cloud_done_outlined,
                color: on ? Aurora.teal : Aurora.textMuted),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Use FarryOn Cloud',
                      style: TextStyle(
                          color: Aurora.textPrimary,
                          fontWeight: FontWeight.w600)),
                  const SizedBox(height: 2),
                  Text(
                    on
                        ? 'Connected to the cloud — nothing else needed.'
                        : 'One tap — no PC needed, always online.',
                    style: const TextStyle(
                        color: Aurora.textMuted, fontSize: 12),
                  ),
                ],
              ),
            ),
            if (on)
              const Icon(Icons.check_circle, color: Aurora.teal, size: 20),
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
      margin: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.34,
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
