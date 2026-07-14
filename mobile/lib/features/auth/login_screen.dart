import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config_store.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/auth.dart';
import '../../state/providers.dart';
import 'signup_screen.dart';

/// Sign-in screen — the app's front door when no FarryOn session exists.
/// Handles the optional 2FA challenge step and exposes a server-address
/// sheet (the backend host must be right before login can work).
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _emailCtl = TextEditingController();
  final _passwordCtl = TextEditingController();
  final _codeCtl = TextEditingController();

  bool _showPw = false;
  bool _busy = false;
  String? _error;
  String? _notice; // e.g. "Account created — sign in to continue."
  String? _pendingToken; // non-null = 2FA challenge step

  Future<void> _openSignup() async {
    final notice = await SignupScreen.open(context);
    if (!mounted || notice == null) return;
    setState(() {
      _notice = notice;
      _error = null;
    });
  }

  @override
  void dispose() {
    _emailCtl.dispose();
    _passwordCtl.dispose();
    _codeCtl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _emailCtl.text.trim();
    final password = _passwordCtl.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = 'Enter your email and password.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await ref.read(authProvider.notifier).signIn(email, password);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result.needsTwoFactor) {
        _pendingToken = result.pendingToken;
      } else if (!result.ok || result.tokens == null) {
        _error = result.message.isNotEmpty
            ? result.message
            : 'Incorrect email or password.';
      }
      // On success the auth gate swaps this screen out — nothing to do here.
    });
  }

  Future<void> _submitCode() async {
    final code = _codeCtl.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'Enter the 6-digit code or a recovery code.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await ref.read(authProvider.notifier).verify2fa(
          pendingToken: _pendingToken!,
          code: code,
          email: _emailCtl.text.trim(),
        );
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (!result.ok) {
        _error = result.message.isNotEmpty
            ? result.message
            : "That code didn't match. Try again.";
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final cfg = ref.watch(configProvider);
    final twoFactor = _pendingToken != null;

    return Scaffold(
      backgroundColor: Aurora.base,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 24, 16, 28),
          children: [
            _hero(twoFactor),
            const SizedBox(height: 24),
            if (_notice != null) ...[
              _noticeBanner(_notice!),
              const SizedBox(height: 16),
            ],
            if (!twoFactor) ..._credentialFields() else ..._codeField(),
            if (_error != null) ...[
              const SizedBox(height: 12),
              _errorBanner(_error!),
            ],
            const SizedBox(height: 18),
            GradientButton(
              label: _busy
                  ? (twoFactor ? 'Verifying…' : 'Signing in…')
                  : (twoFactor ? 'Verify code' : 'Sign in'),
              icon: twoFactor ? Icons.shield_rounded : Icons.login_rounded,
              onPressed: _busy ? null : (twoFactor ? _submitCode : _submit),
            ),
            const SizedBox(height: 10),
            if (twoFactor)
              TextButton(
                onPressed: _busy
                    ? null
                    : () => setState(() {
                          _pendingToken = null;
                          _codeCtl.clear();
                          _error = null;
                        }),
                child: const Text('Back to sign in',
                    style: TextStyle(color: Aurora.textMuted)),
              )
            else ...[
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Text("New to Farry? ",
                      style: TextStyle(color: Aurora.textMuted, fontSize: 13)),
                  TextButton(
                    onPressed: _busy ? null : _openSignup,
                    style: TextButton.styleFrom(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      minimumSize: Size.zero,
                    ),
                    child: const Text('Create an account',
                        style: TextStyle(
                            color: Aurora.mint,
                            fontSize: 13,
                            fontWeight: FontWeight.w700)),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              const Divider(height: 1, color: Aurora.glassBorder),
              const SizedBox(height: 6),
              SettingsRow(
                icon: Icons.dns_rounded,
                gradient: Aurora.gradBlue,
                title: 'Server',
                subtitle:
                    '${cfg.secure ? "https" : "http"}://${cfg.host}:${cfg.port}',
                showDivider: false,
                onTap: _busy ? null : () => _ServerSheet.open(context),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// Gradient hero header — same motif as the settings hub's hero card.
  Widget _hero(bool twoFactor) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 26, 20, 26),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF0F6E56), Color(0xFF1D9E75), Color(0xFF534AB7)],
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(colors: [Aurora.mint, Aurora.tealInk]),
            ),
          ),
          const SizedBox(height: 16),
          Text(
            twoFactor ? 'Two-factor check' : 'Welcome back',
            style: const TextStyle(
                color: Colors.white, fontSize: 22, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            twoFactor
                ? 'Enter the 6-digit code from your authenticator app, or a recovery code.'
                : 'Sign in to sync your notes, reminders, and glasses.',
            style: const TextStyle(
                color: Color(0xFFD6F3E9), fontSize: 12, height: 1.4),
          ),
        ],
      ),
    );
  }

  List<Widget> _credentialFields() => [
        const SectionLabel('Account'),
        const SizedBox(height: 2),
        TextField(
          controller: _emailCtl,
          keyboardType: TextInputType.emailAddress,
          autocorrect: false,
          enabled: !_busy,
          onChanged: (_) => setState(() => _error = null),
          decoration: const InputDecoration(
            labelText: 'Email address',
            hintText: 'you@example.com',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _passwordCtl,
          obscureText: !_showPw,
          autocorrect: false,
          enableSuggestions: false,
          enabled: !_busy,
          onChanged: (_) => setState(() => _error = null),
          onSubmitted: (_) => _busy ? null : _submit(),
          decoration: InputDecoration(
            labelText: 'Password',
            border: const OutlineInputBorder(),
            suffixIcon: IconButton(
              icon: Icon(_showPw ? Icons.visibility_off : Icons.visibility,
                  color: Aurora.textMuted),
              onPressed: () => setState(() => _showPw = !_showPw),
            ),
          ),
        ),
      ];

  List<Widget> _codeField() => [
        const SectionLabel('Verification code'),
        const SizedBox(height: 2),
        TextField(
          controller: _codeCtl,
          keyboardType: TextInputType.text,
          autocorrect: false,
          enableSuggestions: false,
          enabled: !_busy,
          autofocus: true,
          onChanged: (_) => setState(() => _error = null),
          onSubmitted: (_) => _busy ? null : _submitCode(),
          decoration: const InputDecoration(
            labelText: 'Code',
            hintText: '123456',
            border: OutlineInputBorder(),
          ),
        ),
      ];

  /// Mint counterpart of [_errorBanner] — same recipe, positive tint.
  Widget _noticeBanner(String message) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Aurora.mint.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: Aurora.mint.withValues(alpha: 0.3)),
        ),
        child: Row(
          children: [
            const Icon(Icons.check_circle_rounded, color: Aurora.mint, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(message,
                  style: const TextStyle(
                      color: Aurora.mint, fontSize: 12.5, height: 1.4)),
            ),
          ],
        ),
      );

  Widget _errorBanner(String message) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Aurora.danger.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: Aurora.danger.withValues(alpha: 0.3)),
        ),
        child: Row(
          children: [
            const Icon(Icons.error_outline_rounded,
                color: Aurora.danger, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(message,
                  style: const TextStyle(
                      color: Aurora.danger, fontSize: 12.5, height: 1.4)),
            ),
          ],
        ),
      );
}

/// Bottom sheet to point the app at the right backend before signing in.
/// Saves straight to [ConfigStore] — the LiveNotifier (which normally
/// persists config changes) isn't alive before sign-in.
class _ServerSheet extends ConsumerStatefulWidget {
  const _ServerSheet();

  static Future<void> open(BuildContext context) {
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _ServerSheet(),
    );
  }

  @override
  ConsumerState<_ServerSheet> createState() => _ServerSheetState();
}

class _ServerSheetState extends ConsumerState<_ServerSheet> {
  late final _hostCtl =
      TextEditingController(text: ref.read(configProvider).host);
  late final _portCtl =
      TextEditingController(text: ref.read(configProvider).port.toString());
  late bool _secure = ref.read(configProvider).secure;

  @override
  void dispose() {
    _hostCtl.dispose();
    _portCtl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final cfg = ref.read(configProvider);
    final next = cfg.copyWith(
      host: _hostCtl.text.trim(),
      port: int.tryParse(_portCtl.text.trim()) ?? cfg.port,
      secure: _secure,
    );
    ref.read(configProvider.notifier).state = next;
    await ConfigStore.save(next);
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
          16, 18, 16, 18 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Server address'),
          const SizedBox(height: 2),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _hostCtl,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Host',
                    hintText: '192.168.1.50 or api.farryon.app',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              SizedBox(
                width: 104,
                child: TextField(
                  controller: _portCtl,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Port',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Use TLS (https/wss)',
                style: TextStyle(color: Aurora.textPrimary, fontSize: 14)),
            value: _secure,
            activeThumbColor: Aurora.mint,
            onChanged: (v) => setState(() => _secure = v),
          ),
          const SizedBox(height: 8),
          GradientButton(
            label: 'Save',
            icon: Icons.check_rounded,
            onPressed: _save,
          ),
        ],
      ),
    );
  }
}
