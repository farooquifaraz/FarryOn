import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../data/email_probe.dart';
import '../../data/live_client.dart';
import '../../state/auth.dart';
import '../../state/providers.dart';
import '../data/conversations_screen.dart';
import '../data/notes_screen.dart';
import '../data/reminders_screen.dart';
import '../debug/debug_logs_screen.dart';

/// The live, cloud-hosted FarryOn backend (Render). Mirrors the constants the
/// old settings sheet used so the Cloud/Local presets behave identically.
const String _kCloudHost = 'farryon-backend.onrender.com';
const int _kCloudPort = 443;
const String _kLocalHost = '192.168.1.107';
const int _kLocalPort = 8000;

bool _isCloud(AppConfig c) =>
    c.host.trim() == _kCloudHost && c.secure && c.port == _kCloudPort;

String _providerSubtitle(String p) => switch (p) {
      'openai' => 'OpenAI · premium',
      'mock' => 'Mock (offline test)',
      _ => 'Gemini · fast, best value',
    };

/// Redesigned Settings — a grouped hub. Heavy forms (Email, Web search, Server)
/// open as focused sub-pages instead of one long scroll. Every option writes to
/// the exact same [configProvider] / [liveProvider] as before, so behaviour is
/// unchanged — only the presentation is new.
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key, required this.onOpenGlassesLab});

  /// The glasses-lab teardown/reopen dance lives in the live screen (it must
  /// tear the session down first); we call back into it so that logic stays put.
  final VoidCallback onOpenGlassesLab;

  static Future<void> open(
    BuildContext context, {
    required VoidCallback onOpenGlassesLab,
  }) =>
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => SettingsScreen(onOpenGlassesLab: onOpenGlassesLab),
        ),
      );

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cfg = ref.watch(configProvider);
    final live = ref.watch(liveProvider);
    final auth = ref.watch(authProvider);

    final audioGlasses = live.audioKind == 'glasses';
    final videoGlasses = live.videoKind == 'glasses';
    final glassesConnected = live.glassesConnected;
    final glassesInfo = glassesConnected
        ? (live.glassesBattery != null
            ? ' · Glasses ${live.glassesBattery}%'
            : ' · Glasses on')
        : '';
    final devicesSub =
        '${audioGlasses ? 'Glasses' : 'Phone'} mic · ${videoGlasses ? 'Glasses' : 'Phone'} cam$glassesInfo';
    final emailAccts = cfg.emailAccounts;
    final emailSub = emailAccts.isEmpty
        ? 'Not connected'
        : emailAccts.length == 1
            ? emailAccts.first.address
            : '${emailAccts.length} mailboxes';
    final serverSub =
        _isCloud(cfg) ? 'Cloud · Render' : '${cfg.host}:${cfg.port}';

    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
        children: [
          _Hero(connection: live.connection, target: serverSub),
          const SizedBox(height: 22),

          const SectionLabel('Assistant'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.auto_awesome_rounded,
              gradient: Aurora.gradPurple,
              title: 'AI model',
              subtitle: _providerSubtitle(cfg.provider),
              onTap: () => _push(context, const _AiModelPage()),
            ),
            SettingsRow(
              icon: Icons.mic_rounded,
              gradient: Aurora.gradTeal,
              title: 'Voice & mic',
              subtitle: cfg.handsFree
                  ? 'Hands-free · always listening'
                  : 'Tap-to-talk',
              onTap: () => _push(context, const _VoiceMicPage()),
              showDivider: false,
            ),
          ]),
          const SizedBox(height: 20),

          const SectionLabel('Devices'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.devices_other_rounded,
              gradient: Aurora.gradBlue,
              title: 'Capture devices',
              subtitle: devicesSub,
              subtitleColor: glassesConnected ? Aurora.mint : null,
              onTap: () => _push(context, const _DevicesPage()),
              showDivider: false,
            ),
          ]),
          const SizedBox(height: 20),

          const SectionLabel('Connections'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.mail_rounded,
              gradient: Aurora.gradCoral,
              title: 'Email inbox',
              subtitle: emailSub,
              onTap: () => _push(context, const _EmailPage()),
            ),
            SettingsRow(
              icon: Icons.travel_explore_rounded,
              gradient: Aurora.gradPurple,
              title: 'Web search',
              subtitle: cfg.webSearchProvider,
              onTap: () => _push(context, const _WebSearchPage()),
            ),
            SettingsRow(
              icon: Icons.cloud_rounded,
              gradient: Aurora.gradBlue,
              title: 'Server',
              subtitle: serverSub,
              onTap: () => _push(context, const _ServerPage()),
              showDivider: false,
            ),
          ]),
          const SizedBox(height: 20),

          const SectionLabel('Your stuff'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.sticky_note_2_rounded,
              gradient: Aurora.gradGreen,
              title: 'Notes',
              subtitle: 'Things Farry remembered for you',
              onTap: () => NotesScreen.open(context),
            ),
            SettingsRow(
              icon: Icons.alarm_rounded,
              gradient: Aurora.gradAmber,
              title: 'Reminders',
              subtitle: 'Time-based reminders & tasks',
              onTap: () => RemindersScreen.open(context),
            ),
            SettingsRow(
              icon: Icons.forum_rounded,
              gradient: Aurora.gradPurple,
              title: 'Conversations',
              subtitle: 'Read your past chats',
              onTap: () => ConversationsScreen.open(context),
              showDivider: false,
            ),
          ]),
          const SizedBox(height: 20),

          const SectionLabel('Account'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.account_circle_rounded,
              gradient: Aurora.gradPink,
              title: auth.displayName?.trim().isNotEmpty == true
                  ? auth.displayName!
                  : (auth.email.isNotEmpty ? auth.email : 'Signed in'),
              subtitle: auth.email,
              trailing: const SizedBox.shrink(),
            ),
            SettingsRow(
              icon: Icons.logout_rounded,
              gradient: Aurora.gradCoral,
              title: 'Sign out',
              subtitle: 'Ends this device\'s session',
              showDivider: false,
              onTap: () async {
                final confirmed = await showDialog<bool>(
                  context: context,
                  builder: (ctx) => AlertDialog(
                    backgroundColor: Aurora.surfaceHigh,
                    title: const Text('Sign out?',
                        style: TextStyle(color: Aurora.textPrimary)),
                    content: const Text(
                        'You\'ll need to sign in again to use Farry.',
                        style: TextStyle(color: Aurora.textMuted)),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.of(ctx).pop(false),
                        child: const Text('Cancel',
                            style: TextStyle(color: Aurora.textMuted)),
                      ),
                      TextButton(
                        onPressed: () => Navigator.of(ctx).pop(true),
                        child: const Text('Sign out',
                            style: TextStyle(color: Aurora.danger)),
                      ),
                    ],
                  ),
                );
                if (confirmed != true || !context.mounted) return;
                // signOut() tears the live session down itself (timeout-
                // guarded) before dropping the token — see AuthNotifier.
                await ref.read(authProvider.notifier).signOut();
                // The auth gate now shows LoginScreen underneath — unwind
                // the settings stack back to it.
                if (context.mounted) {
                  Navigator.of(context).popUntil((r) => r.isFirst);
                }
              },
            ),
          ]),
          const SizedBox(height: 20),

          const SectionLabel('About'),
          SettingsGroup(children: [
            SettingsRow(
              icon: Icons.bug_report_rounded,
              gradient: Aurora.gradAmber,
              title: 'Debug logs',
              subtitle: 'View / share the tool + error trail',
              onTap: () => DebugLogsScreen.open(context),
              showDivider: kDebugMode,
            ),
            if (kDebugMode)
              SettingsRow(
                icon: Icons.science_rounded,
                gradient: Aurora.gradPurple,
                title: 'Glasses Lab',
                subtitle: 'L801 hardware test bench (debug only)',
                onTap: onOpenGlassesLab,
              ),
            SettingsRow(
              icon: Icons.info_rounded,
              gradient: Aurora.gradGreen,
              title: 'Version',
              subtitle: cfg.appVersion,
              trailing: const SizedBox.shrink(),
              showDivider: false,
            ),
          ]),
        ],
      ),
    );
  }

  void _push(BuildContext context, Widget page) => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => page),
      );
}

/// Gradient hero card at the top of the hub: shows the connection state and the
/// backend it points at, with a soft orb glyph.
class _Hero extends StatelessWidget {
  const _Hero({required this.connection, required this.target});

  final ConnectionStatus connection;
  final String target;

  @override
  Widget build(BuildContext context) {
    final (color, label) = switch (connection) {
      ConnectionStatus.connected => (Aurora.mint, 'Connected'),
      ConnectionStatus.connecting => (Aurora.amber, 'Connecting…'),
      ConnectionStatus.reconnecting => (Aurora.amber, 'Reconnecting…'),
      ConnectionStatus.disconnected => (Aurora.danger, 'Offline'),
    };
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF0F6E56), Color(0xFF1D9E75), Color(0xFF534AB7)],
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(colors: [Aurora.mint, Aurora.tealInk]),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Farry',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.w600)),
                const SizedBox(height: 3),
                Row(
                  children: [
                    Icon(Icons.circle, size: 8, color: color),
                    const SizedBox(width: 6),
                    Text('$label · $target',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: Color(0xFFD6F3E9), fontSize: 12)),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Shared scaffold for a settings sub-page: dark app bar + a scrollable body
/// with a pinned gradient Save bar (kept reachable when the keyboard opens).
class _SubPage extends StatelessWidget {
  const _SubPage({
    required this.title,
    required this.children,
    this.onSave,
  });

  final String title;
  final List<Widget> children;
  final VoidCallback? onSave;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: Text(title)),
      body: Column(
        children: [
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
              children: children,
            ),
          ),
          if (onSave != null)
            Container(
              padding: EdgeInsets.fromLTRB(
                16,
                10,
                16,
                10 + MediaQuery.of(context).padding.bottom,
              ),
              decoration: const BoxDecoration(
                color: Aurora.surface,
                border: Border(top: BorderSide(color: Aurora.glassBorder)),
              ),
              child: GradientButton(
                label: 'Save & reconnect',
                icon: Icons.check_rounded,
                onPressed: onSave,
              ),
            ),
        ],
      ),
    );
  }
}

Text _fieldLabel(String t) => Text(
      t.toUpperCase(),
      style: const TextStyle(
        color: Aurora.mint,
        fontSize: 12,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.6,
      ),
    );

/// A clearly-visible selectable pill (replaces the low-contrast default
/// ChoiceChip): glass with a hairline at rest, teal gradient + dark ink when
/// selected — so every option reads at a glance in the dark theme.
class _SelectPill extends StatelessWidget {
  const _SelectPill({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(22),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
        decoration: BoxDecoration(
          gradient: selected ? Aurora.gradTeal : null,
          color: selected ? null : Aurora.glass,
          borderRadius: BorderRadius.circular(22),
          border: Border.all(
            color: selected ? Colors.transparent : Aurora.glassBorder,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? Aurora.tealInk : Aurora.textPrimary,
            fontSize: 13,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          ),
        ),
      ),
    );
  }
}

// ============================ AI model ==============================

class _AiModelPage extends ConsumerStatefulWidget {
  const _AiModelPage();
  @override
  ConsumerState<_AiModelPage> createState() => _AiModelPageState();
}

class _AiModelPageState extends ConsumerState<_AiModelPage> {
  late String _provider = ref.read(configProvider).provider;

  void _save() {
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state = cfg.copyWith(provider: _provider);
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return _SubPage(
      title: 'AI model',
      onSave: _save,
      children: [
        _fieldLabel('Provider'),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final p in const [
              ('Gemini ⚡', 'gemini'),
              ('OpenAI ⚡', 'openai'),
              ('Mock', 'mock'),
            ])
              _SelectPill(
                label: p.$1,
                selected: _provider == p.$2,
                onTap: () => setState(() => _provider = p.$2),
              ),
          ],
        ),
        const SizedBox(height: 12),
        const Text(
          'Gemini & OpenAI are fast with smooth voice + vision. '
          'Gemini is the best value (cheapest); OpenAI is premium.',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.4),
        ),
      ],
    );
  }
}

// ============================ Voice & mic ===========================

class _VoiceMicPage extends ConsumerStatefulWidget {
  const _VoiceMicPage();
  @override
  ConsumerState<_VoiceMicPage> createState() => _VoiceMicPageState();
}

class _VoiceMicPageState extends ConsumerState<_VoiceMicPage> {
  late bool _handsFree = ref.read(configProvider).handsFree;

  void _save() {
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(handsFree: _handsFree);
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return _SubPage(
      title: 'Voice & mic',
      onSave: _save,
      children: [
        SettingsGroup(children: [
          SettingsRow(
            icon: _handsFree ? Icons.hearing_rounded : Icons.touch_app_rounded,
            gradient: Aurora.gradTeal,
            title: 'Hands-free mic',
            subtitle: _handsFree
                ? 'Always listening (best in a quiet room)'
                : 'Tap-to-talk: mic opens only when you tap it',
            showDivider: false,
            trailing: Switch(
              value: _handsFree,
              activeThumbColor: Aurora.mint,
              onChanged: (v) => setState(() => _handsFree = v),
            ),
          ),
        ]),
        const SizedBox(height: 12),
        const Text(
          'Tap-to-talk is best with background noise or a TV — the mic stays '
          'closed until you tap it, so phantom turns can never trigger.',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.4),
        ),
      ],
    );
  }
}

// ============================ Devices ===============================

class _DevicesPage extends ConsumerWidget {
  const _DevicesPage();

  static CaptureDeviceKind _kind(String name) =>
      name == 'glasses' ? CaptureDeviceKind.glasses : CaptureDeviceKind.phone;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(liveProvider);
    final notifier = ref.read(liveProvider.notifier);
    final audio = _kind(state.audioKind);
    final video = _kind(state.videoKind);

    return _SubPage(
      title: 'Capture devices',
      children: [
        const Text(
          'Mic and camera pick independently — e.g. earbuds mic + glasses camera.',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.4),
        ),
        const SizedBox(height: 18),
        _fieldLabel('Microphone'),
        const SizedBox(height: 10),
        SettingsGroup(children: [
          _OptionRow(
            icon: Icons.mic_rounded,
            gradient: Aurora.gradTeal,
            title: 'Phone / earbuds mic',
            selected: audio == CaptureDeviceKind.phone,
            onTap: () => notifier.setAudioDevice(CaptureDeviceKind.phone),
          ),
          _OptionRow(
            icon: Icons.visibility_rounded,
            gradient: Aurora.gradTeal,
            title: 'Glasses mic (long-press to talk)',
            selected: audio == CaptureDeviceKind.glasses,
            onTap: () => notifier.setAudioDevice(CaptureDeviceKind.glasses),
            showDivider: false,
          ),
        ]),
        const SizedBox(height: 18),
        _fieldLabel('Camera'),
        const SizedBox(height: 10),
        SettingsGroup(children: [
          _OptionRow(
            icon: Icons.photo_camera_rounded,
            gradient: Aurora.gradBlue,
            title: 'Phone camera (live 1 fps)',
            selected: video == CaptureDeviceKind.phone,
            onTap: () => notifier.setVideoDevice(CaptureDeviceKind.phone),
          ),
          _OptionRow(
            icon: Icons.visibility_rounded,
            gradient: Aurora.gradBlue,
            title: 'Glasses camera (say "what is this" or tap 📷)',
            selected: video == CaptureDeviceKind.glasses,
            onTap: () => notifier.setVideoDevice(CaptureDeviceKind.glasses),
            showDivider: false,
          ),
        ]),
      ],
    );
  }
}

class _OptionRow extends StatelessWidget {
  const _OptionRow({
    required this.icon,
    required this.gradient,
    required this.title,
    required this.selected,
    required this.onTap,
    this.showDivider = true,
  });

  final IconData icon;
  final Gradient gradient;
  final String title;
  final bool selected;
  final VoidCallback onTap;
  final bool showDivider;

  @override
  Widget build(BuildContext context) {
    return SettingsRow(
      icon: icon,
      gradient: gradient,
      title: title,
      onTap: onTap,
      showDivider: showDivider,
      trailing: Icon(
        selected
            ? Icons.check_circle_rounded
            : Icons.radio_button_unchecked_rounded,
        color: selected ? Aurora.teal : Aurora.textMuted,
      ),
    );
  }
}

// ============================ Server ================================

class _ServerPage extends ConsumerStatefulWidget {
  const _ServerPage();
  @override
  ConsumerState<_ServerPage> createState() => _ServerPageState();
}

class _ServerPageState extends ConsumerState<_ServerPage> {
  late final AppConfig _initial = ref.read(configProvider);
  late final _hostCtl = TextEditingController(text: _initial.host);
  late final _portCtl = TextEditingController(text: _initial.port.toString());
  late bool _secure = _initial.secure;

  @override
  void dispose() {
    _hostCtl.dispose();
    _portCtl.dispose();
    super.dispose();
  }

  bool get _cloud =>
      _hostCtl.text.trim() == _kCloudHost && _secure && _portCtl.text == '443';

  void _useCloud() => setState(() {
        _hostCtl.text = _kCloudHost;
        _portCtl.text = '$_kCloudPort';
        _secure = true;
      });

  void _useLocal() => setState(() {
        _hostCtl.text = _kLocalHost;
        _portCtl.text = '$_kLocalPort';
        _secure = false;
      });

  void _save() {
    final cfg = ref.read(configProvider);
    final port = int.tryParse(_portCtl.text.trim()) ?? cfg.port;
    ref.read(configProvider.notifier).state = cfg.copyWith(
      host: _hostCtl.text.trim(),
      port: port,
      secure: _secure,
    );
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return _SubPage(
      title: 'Server',
      onSave: _save,
      children: [
        _connectionStatus(),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: _modeChip(
                label: 'Cloud',
                subtitle: 'Always online',
                icon: Icons.cloud_outlined,
                selected: _cloud,
                onTap: _useCloud,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _modeChip(
                label: 'Local (Dev)',
                subtitle: 'Your PC on Wi-Fi',
                icon: Icons.lan_outlined,
                selected: !_cloud,
                onTap: _useLocal,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
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
        const SizedBox(height: 12),
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
                activeThumbColor: Aurora.mint,
                onChanged: (v) => setState(() => _secure = v),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _connectionStatus() {
    final status = ref.watch(liveProvider.select((s) => s.connection));
    final (color, icon, label) = switch (status) {
      ConnectionStatus.connected =>
        (Aurora.teal, Icons.check_circle, 'Connected'),
      ConnectionStatus.connecting => (Aurora.amber, Icons.sync, 'Connecting…'),
      ConnectionStatus.reconnecting =>
        (Aurora.amber, Icons.sync, 'Reconnecting…'),
      ConnectionStatus.disconnected =>
        (Aurora.danger, Icons.error_outline, 'Offline'),
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
                    style:
                        TextStyle(color: color, fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(target,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        const TextStyle(color: Aurora.textMuted, fontSize: 12)),
              ],
            ),
          ),
        ],
      ),
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
                        color:
                            selected ? Aurora.textPrimary : Aurora.textMuted,
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

// ============================ Email =================================

/// Accounts hub: lists the configured mailboxes (0–2), each opening an editor.
/// Watches [configProvider] so add / edit / delete reflect immediately.
class _EmailPage extends ConsumerWidget {
  const _EmailPage();

  static const int _maxAccounts = 2;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accounts = ref.watch(configProvider).emailAccounts;
    return _SubPage(
      title: 'Email accounts',
      children: [
        const Text(
          'Connect up to two mailboxes — say a personal and a work inbox. '
          'Farry reads your primary one by default, or the one you name '
          '("check my work email").',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.5),
        ),
        const SizedBox(height: 18),
        if (accounts.isEmpty)
          _emptyState(context)
        else ...[
          _fieldLabel('Connected mailboxes'),
          const SizedBox(height: 10),
          for (final a in accounts) _AccountCard(account: a),
        ],
        const SizedBox(height: 4),
        if (accounts.length < _maxAccounts)
          _AddAccountButton(
            onTap: () => _open(context, null),
          )
        else
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 6),
            child: Text(
              'Maximum of 2 mailboxes · remove one to add another.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Aurora.textMuted, fontSize: 12),
            ),
          ),
        const SizedBox(height: 22),
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Aurora.glass,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Aurora.glassBorder),
          ),
          child: const Text(
            'When sending, if you don\'t name an account Farry asks which one '
            'to send from — so a reply never leaves the wrong address.',
            style: TextStyle(color: Aurora.textMuted, fontSize: 12, height: 1.5),
          ),
        ),
      ],
    );
  }

  Widget _emptyState(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(vertical: 26, horizontal: 16),
        alignment: Alignment.center,
        child: const Text(
          'No mailbox connected yet.',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13),
        ),
      );

  static void _open(BuildContext context, EmailAccount? account) =>
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => _EmailAccountEditPage(account: account),
        ),
      );
}

class _AccountCard extends StatelessWidget {
  const _AccountCard({required this.account});
  final EmailAccount account;

  @override
  Widget build(BuildContext context) {
    final providerLabel =
        EmailProviders.presets[account.provider]?.label ?? account.provider;
    final ready = account.isComplete;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        onTap: () => _EmailPage._open(context, account),
        borderRadius: BorderRadius.circular(14),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Aurora.glass,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: account.primary
                  ? Aurora.mint.withValues(alpha: 0.4)
                  : Aurora.glassBorder,
            ),
          ),
          child: Row(
            children: [
              Container(
                width: 44,
                height: 44,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  gradient: Aurora.gradCoral,
                  borderRadius: BorderRadius.circular(11),
                ),
                child: Text(
                  (account.label.isNotEmpty ? account.label[0] : '?')
                      .toUpperCase(),
                  style: const TextStyle(
                    color: Aurora.tealInk,
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            account.label,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: Aurora.textPrimary,
                              fontSize: 15,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        if (account.primary) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 7, vertical: 2),
                            decoration: BoxDecoration(
                              color: Aurora.mint.withValues(alpha: 0.14),
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(
                                  color: Aurora.mint.withValues(alpha: 0.3)),
                            ),
                            child: const Text(
                              'PRIMARY',
                              style: TextStyle(
                                color: Aurora.mint,
                                fontSize: 9,
                                fontWeight: FontWeight.w700,
                                letterSpacing: 0.6,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      account.address,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          color: Aurora.textMuted, fontSize: 12),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Container(
                          width: 6,
                          height: 6,
                          decoration: BoxDecoration(
                            color: ready ? Aurora.mint : Aurora.textMuted,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          ready ? 'Ready' : 'Needs app password',
                          style: const TextStyle(
                              color: Aurora.textMuted, fontSize: 11),
                        ),
                        const Spacer(),
                        Text(
                          providerLabel,
                          style: const TextStyle(
                              color: Aurora.textMuted, fontSize: 11),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded,
                  color: Aurora.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}

class _AddAccountButton extends StatelessWidget {
  const _AddAccountButton({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(13),
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(13),
          border: Border.all(
            color: Aurora.textMuted.withValues(alpha: 0.4),
            style: BorderStyle.solid,
          ),
        ),
        child: const Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.add_rounded, color: Aurora.textMuted, size: 20),
            SizedBox(width: 8),
            Text('Add account',
                style: TextStyle(
                    color: Aurora.textMuted,
                    fontSize: 14,
                    fontWeight: FontWeight.w500)),
          ],
        ),
      ),
    );
  }
}

/// Add or edit one mailbox: label, provider, address, app password, custom
/// hosts, a live "Test connection" probe, and the primary toggle.
class _EmailAccountEditPage extends ConsumerStatefulWidget {
  const _EmailAccountEditPage({this.account});
  final EmailAccount? account;

  @override
  ConsumerState<_EmailAccountEditPage> createState() =>
      _EmailAccountEditPageState();
}

class _EmailAccountEditPageState
    extends ConsumerState<_EmailAccountEditPage> {
  late final EmailAccount? _existing = widget.account;
  late final _labelCtl =
      TextEditingController(text: _existing?.label ?? '');
  late final _emailCtl =
      TextEditingController(text: _existing?.address ?? '');
  late final _pwCtl =
      TextEditingController(text: _existing?.appPassword ?? '');
  late final _imapCtl =
      TextEditingController(text: _existing?.imapHost ?? '');
  late final _smtpCtl =
      TextEditingController(text: _existing?.smtpHost ?? '');
  late final _smtpPortCtl =
      TextEditingController(text: (_existing?.smtpPort ?? 587).toString());
  late String _provider = _existing?.provider ?? 'gmail';
  late bool _primary = _existing?.primary ?? false;
  bool _showPw = false;

  bool _testing = false;
  EmailProbeResult? _testResult;

  bool get _isOnlyAccount {
    final accts = ref.read(configProvider).emailAccounts;
    if (_existing == null) return accts.isEmpty; // first account being added
    return accts.length == 1 && accts.first.id == _existing.id;
  }

  @override
  void dispose() {
    _labelCtl.dispose();
    _emailCtl.dispose();
    _pwCtl.dispose();
    _imapCtl.dispose();
    _smtpCtl.dispose();
    _smtpPortCtl.dispose();
    super.dispose();
  }

  EmailAccount _buildAccount() {
    final custom = _provider == 'custom';
    final label = _labelCtl.text.trim();
    return EmailAccount(
      id: _existing?.id ?? EmailAccount.newId(),
      label: label.isEmpty ? 'Email' : label,
      address: _emailCtl.text.trim(),
      appPassword: _pwCtl.text.trim(),
      provider: _provider,
      imapHost: custom ? _imapCtl.text.trim() : null,
      smtpHost: custom ? _smtpCtl.text.trim() : null,
      smtpPort: custom ? (int.tryParse(_smtpPortCtl.text.trim()) ?? 587) : 587,
      primary: _primary || _isOnlyAccount,
    );
  }

  Future<void> _test() async {
    setState(() {
      _testing = true;
      _testResult = null;
    });
    final a = _buildAccount();
    final result = await testImapLogin(
      host: a.resolvedImapHost,
      address: a.address,
      password: a.appPassword ?? '',
    );
    if (mounted) {
      setState(() {
        _testing = false;
        _testResult = result;
      });
    }
  }

  void _save() {
    final cfg = ref.read(configProvider);
    final edited = _buildAccount();
    final list = [...cfg.emailAccounts];
    final idx = list.indexWhere((a) => a.id == edited.id);
    if (idx >= 0) {
      list[idx] = edited;
    } else {
      list.add(edited);
    }
    ref.read(configProvider.notifier).state = cfg.copyWith(
      emailAccounts: _normalizePrimary(
        list,
        preferId: edited.primary ? edited.id : null,
      ),
    );
    Navigator.pop(context);
  }

  void _delete() {
    final cfg = ref.read(configProvider);
    final list =
        cfg.emailAccounts.where((a) => a.id != _existing!.id).toList();
    ref.read(configProvider.notifier).state = cfg.copyWith(
      emailAccounts: _normalizePrimary(list),
    );
    Navigator.pop(context);
  }

  /// Keep exactly one account flagged primary. Prefer [preferId]; else keep the
  /// existing primary; else elect the first. Empty list stays empty.
  static List<EmailAccount> _normalizePrimary(
    List<EmailAccount> list, {
    String? preferId,
  }) {
    if (list.isEmpty) return list;
    final primaryId = preferId ??
        list.firstWhere((a) => a.primary, orElse: () => list.first).id;
    return [for (final a in list) a.copyWith(primary: a.id == primaryId)];
  }

  @override
  Widget build(BuildContext context) {
    final custom = _provider == 'custom';
    final result = _testResult;
    return _SubPage(
      title: _existing == null ? 'Add account' : 'Edit account',
      onSave: _save,
      children: [
        _fieldLabel('Label'),
        const SizedBox(height: 8),
        TextField(
          controller: _labelCtl,
          autocorrect: false,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(
            hintText: 'Personal, Work…',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 18),
        _fieldLabel('Provider'),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final e in EmailProviders.presets.entries)
              _SelectPill(
                label: e.value.label,
                selected: _provider == e.key,
                onTap: () => setState(() {
                  _provider = e.key;
                  _testResult = null;
                }),
              ),
          ],
        ),
        const SizedBox(height: 18),
        _fieldLabel('Account'),
        const SizedBox(height: 10),
        TextField(
          controller: _emailCtl,
          keyboardType: TextInputType.emailAddress,
          autocorrect: false,
          onChanged: (_) => setState(() => _testResult = null),
          decoration: const InputDecoration(
            labelText: 'Email address',
            hintText: 'you@example.com',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _pwCtl,
          obscureText: !_showPw,
          autocorrect: false,
          enableSuggestions: false,
          onChanged: (_) => setState(() => _testResult = null),
          decoration: InputDecoration(
            labelText: 'App password',
            helperText: _provider == 'gmail'
                ? '16-digit App Password, not your login password'
                : 'App password or mailbox password',
            helperMaxLines: 2,
            border: const OutlineInputBorder(),
            suffixIcon: IconButton(
              icon: Icon(
                _showPw ? Icons.visibility_off : Icons.visibility,
                color: Aurora.textMuted,
              ),
              onPressed: () => setState(() => _showPw = !_showPw),
            ),
          ),
        ),
        if (custom) ...[
          const SizedBox(height: 12),
          TextField(
            controller: _imapCtl,
            autocorrect: false,
            onChanged: (_) => setState(() => _testResult = null),
            decoration: const InputDecoration(
              labelText: 'IMAP host (incoming)',
              hintText: 'mail.yourdomain.com',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
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
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: _testing ? null : _test,
          icon: _testing
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.wifi_tethering_rounded, size: 18),
          label: Text(_testing ? 'Testing…' : 'Test connection'),
        ),
        if (result != null) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: (result.ok ? Aurora.mint : Aurora.danger)
                  .withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: (result.ok ? Aurora.mint : Aurora.danger)
                    .withValues(alpha: 0.3),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  result.ok
                      ? Icons.check_circle_rounded
                      : Icons.error_outline_rounded,
                  color: result.ok ? Aurora.mint : Aurora.danger,
                  size: 18,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    result.message,
                    style: TextStyle(
                      color: result.ok ? Aurora.mint : Aurora.danger,
                      fontSize: 12.5,
                      height: 1.4,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
        const SizedBox(height: 18),
        Container(
          decoration: BoxDecoration(
            color: Aurora.glass,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Aurora.glassBorder),
          ),
          child: SwitchListTile(
            value: _primary || _isOnlyAccount,
            onChanged: _isOnlyAccount
                ? null
                : (v) => setState(() => _primary = v),
            activeThumbColor: Aurora.mint,
            title: const Text('Set as primary',
                style: TextStyle(
                    color: Aurora.textPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w500)),
            subtitle: Text(
              _isOnlyAccount
                  ? 'Your only mailbox — always primary'
                  : 'Farry reads & sends from this account by default',
              style: const TextStyle(color: Aurora.textMuted, fontSize: 12),
            ),
          ),
        ),
        if (_existing != null) ...[
          const SizedBox(height: 18),
          TextButton.icon(
            onPressed: _delete,
            icon: const Icon(Icons.delete_outline_rounded,
                color: Aurora.danger, size: 18),
            label: const Text('Remove account',
                style: TextStyle(color: Aurora.danger)),
          ),
        ],
      ],
    );
  }
}

// ============================ Web search ============================

class _WebSearchPage extends ConsumerStatefulWidget {
  const _WebSearchPage();
  @override
  ConsumerState<_WebSearchPage> createState() => _WebSearchPageState();
}

class _WebSearchPageState extends ConsumerState<_WebSearchPage> {
  late final AppConfig _initial = ref.read(configProvider);
  late final _keyCtl =
      TextEditingController(text: _initial.webSearchApiKey ?? '');
  late final _fbKeyCtl =
      TextEditingController(text: _initial.webSearchFallbackApiKey ?? '');
  late String _provider = _initial.webSearchProvider;

  @override
  void dispose() {
    _keyCtl.dispose();
    _fbKeyCtl.dispose();
    super.dispose();
  }

  void _save() {
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state = cfg.copyWith(
      webSearchProvider: _provider,
      webSearchApiKey: _keyCtl.text.trim(),
      webSearchFallbackApiKey: _fbKeyCtl.text.trim(),
    );
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return _SubPage(
      title: 'Web search',
      onSave: _save,
      children: [
        _fieldLabel('Provider'),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final p in const [
              ('Tavily', 'tavily'),
              ('Serper', 'serper'),
              ('SerpAPI', 'serpapi'),
            ])
              _SelectPill(
                label: p.$1,
                selected: _provider == p.$2,
                onTap: () => setState(() => _provider = p.$2),
              ),
          ],
        ),
        const SizedBox(height: 16),
        _fieldLabel('API keys'),
        const SizedBox(height: 10),
        TextField(
          controller: _keyCtl,
          obscureText: true,
          decoration: InputDecoration(
            labelText: '$_provider API key',
            hintText: 'blank = use server default',
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _fbKeyCtl,
          obscureText: true,
          decoration: const InputDecoration(
            labelText: 'Fallback API key (optional)',
            hintText: 'used when the primary runs out of free credits',
            border: OutlineInputBorder(),
          ),
        ),
      ],
    );
  }
}
