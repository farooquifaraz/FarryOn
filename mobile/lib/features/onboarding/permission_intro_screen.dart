/// What FarryOn needs, said once, before it starts asking.
///
/// Android's own advice is to ask for a permission at the moment it is needed,
/// and each feature here still does. What that leaves out is the beginning:
/// someone who has just made an account has no idea the app wants a
/// microphone, a camera, Bluetooth and notifications, so the first prompt
/// arrives with no context and the rest never arrive at all — the glasses were
/// unreachable and reminders were silent on a fresh install because nothing
/// had reached the code that asks (vivo V2246, 2026-08-15).
///
/// So this explains first and asks second, in one pass, with a line of reason
/// per permission. Declining is a normal outcome: the flag records that this
/// was SHOWN, not that anything was granted, and every feature keeps its own
/// in-context request for later.
library;

import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/config_store.dart';
import '../../core/theme.dart';

/// One line per thing we are about to ask for.
class _Ask {
  const _Ask(this.icon, this.title, this.why, this.permissions);
  final IconData icon;
  final String title;
  final String why;
  final List<Permission> permissions;
}

const _asks = <_Ask>[
  _Ask(
    Icons.mic_none,
    'Microphone',
    'So you can talk to Farry instead of typing.',
    [Permission.microphone],
  ),
  _Ask(
    Icons.photo_camera_outlined,
    'Camera',
    'Only when you ask about something in front of you. It closes again '
        'straight after the picture.',
    [Permission.camera],
  ),
  _Ask(
    Icons.bluetooth,
    'Nearby devices',
    'To find and connect your glasses. Without it they cannot be seen at all.',
    [Permission.bluetoothScan, Permission.bluetoothConnect],
  ),
  _Ask(
    Icons.notifications_none,
    'Notifications',
    'So your reminders can actually reach you.',
    [Permission.notification],
  ),
];

class PermissionIntroScreen extends StatefulWidget {
  const PermissionIntroScreen({super.key, required this.onDone});

  /// Called once the pass is over — granted, declined or skipped alike.
  final VoidCallback onDone;

  @override
  State<PermissionIntroScreen> createState() => _PermissionIntroScreenState();
}

class _PermissionIntroScreenState extends State<PermissionIntroScreen> {
  bool _busy = false;

  Future<void> _requestAll() async {
    setState(() => _busy = true);
    for (final ask in _asks) {
      try {
        await ask.permissions.request();
      } catch (_) {
        // A platform that has no such permission must not stop the rest.
        // Whatever is missed here is asked for again by the feature that
        // needs it, which is the path that existed before this screen.
      }
    }
    await ConfigStore.markPermissionIntroSeen();
    if (mounted) widget.onDone();
  }

  Future<void> _skip() async {
    // Also marked as seen. Someone who says "not now" has answered; asking
    // again on every launch would be nagging, and each feature will ask in
    // context anyway.
    await ConfigStore.markPermissionIntroSeen();
    if (mounted) widget.onDone();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 32, 24, 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'What Farry needs',
                style: TextStyle(
                  fontSize: 26,
                  fontWeight: FontWeight.w700,
                  color: Aurora.textPrimary,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Four things, and what each one is for. You can say no to any '
                'of them — the rest still work.',
                style: TextStyle(fontSize: 14, color: Aurora.textMuted),
              ),
              const SizedBox(height: 24),
              Expanded(
                child: ListView.separated(
                  itemCount: _asks.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 14),
                  itemBuilder: (_, i) => _AskRow(ask: _asks[i]),
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _busy ? null : _requestAll,
                  style: FilledButton.styleFrom(
                    backgroundColor: Aurora.teal,
                    foregroundColor: Aurora.tealInk,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  child: Text(_busy ? 'Asking…' : 'Continue'),
                ),
              ),
              TextButton(
                onPressed: _busy ? null : _skip,
                child: const Text(
                  'Not now',
                  style: TextStyle(color: Aurora.textMuted),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AskRow extends StatelessWidget {
  const _AskRow({required this.ask});
  final _Ask ask;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Aurora.surfaceHigh,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(ask.icon, color: Aurora.mint, size: 22),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  ask.title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                    color: Aurora.textPrimary,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  ask.why,
                  style: const TextStyle(
                    fontSize: 13,
                    height: 1.35,
                    color: Aurora.textMuted,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
