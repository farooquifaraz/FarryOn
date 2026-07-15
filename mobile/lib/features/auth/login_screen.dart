

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config_store.dart';
import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/auth.dart';
import '../../state/providers.dart';
import 'signup_screen.dart';
import 'widgets/auth_bits.dart';
import 'widgets/auth_scaffold.dart';

/// Sign-in — the app's front door when no FarryOn session exists.
///
/// Three ways in, in order of how most people will use it: Google, then email
/// + password, then (if the account has 2FA) a code step. The server address
/// lives at the bottom because it's a developer control, but it has to be
/// reachable: nothing here can work while the app points at the wrong backend,
/// and the user can't get to Settings before signing in.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _emailCtl = TextEditingController();
  final _passwordCtl = TextEditingController();
  final _codeCtl = TextEditingController();
  final _passwordFocus = FocusNode();

  bool _showPw = false;
  bool _busy = false;
  bool _googleBusy = false;
  String? _error;
  String? _notice;
  String? _pendingToken; // non-null = 2FA challenge step

  bool get _anyBusy => _busy || _googleBusy;

  @override
  void dispose() {
    _emailCtl.dispose();
    _passwordCtl.dispose();
    _codeCtl.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  void _clearMessages() {
    if (_error != null || _notice != null) {
      setState(() {
        _error = null;
        _notice = null;
      });
    }
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
      _notice = null;
    });
    final result = await ref.read(authProvider.notifier).signIn(email, password);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result.needsTwoFactor) {
        _pendingToken = result.pendingToken;
      } else if (result.tokens == null) {
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

  Future<void> _google() async {
    setState(() {
      _googleBusy = true;
      _error = null;
      _notice = null;
    });
    final result = await ref.read(authProvider.notifier).signInWithGoogle();
    if (!mounted) return;
    setState(() {
      _googleBusy = false;
      // A cancel is the user's own choice — saying "sign-in failed" for it
      // would be blaming them for a button they meant to press.
      if (!result.ok && !result.cancelled) {
        _error = result.message.isNotEmpty
            ? result.message
            : "Google sign-in didn't work. Try again.";
      }
    });
  }

  Future<void> _openSignup() async {
    final notice = await SignupScreen.open(context);
    if (!mounted || notice == null) return;
    setState(() {
      _notice = notice;
      _error = null;
    });
  }

  void _leaveTwoFactor() {
    setState(() {
      _pendingToken = null;
      _codeCtl.clear();
      _error = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final twoFactor = _pendingToken != null;

    return AuthScaffold(
      // During the 2FA step, system-back returns to the credentials form
      // rather than dropping the user out of the app mid-sign-in.
      child: PopScope(
        canPop: !twoFactor,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop && twoFactor) _leaveTwoFactor();
        },
        child: AuthForm(
          children: [
            const AuthBrand(),
            const SizedBox(height: 34),
            Text(
              twoFactor ? 'Two-factor check' : 'Welcome back',
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Aurora.textPrimary,
                fontSize: 27,
                fontWeight: FontWeight.w700,
                letterSpacing: -0.5,
              ),
            ),
            const SizedBox(height: 7),
            Text(
              twoFactor
                  ? 'Enter the 6-digit code from your authenticator app, or one of your recovery codes.'
                  : 'Sign in to sync your notes, reminders, and glasses.',
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Aurora.textMuted,
                fontSize: 13.5,
                height: 1.45,
              ),
            ),
            const SizedBox(height: 28),
            if (twoFactor) ..._codeStep() else ..._credentialsStep(),
            if (_notice != null) ...[
              const SizedBox(height: 14),
              AuthBanner.success(_notice!),
            ],
            if (_error != null) ...[
              const SizedBox(height: 14),
              AuthBanner.error(_error!),
            ],
            const SizedBox(height: 22),
            GradientButton(
              label: _busy
                  ? (twoFactor ? 'Verifying…' : 'Signing in…')
                  : (twoFactor ? 'Verify code' : 'Sign in'),
              icon: twoFactor ? Icons.shield_rounded : Icons.arrow_forward_rounded,
              onPressed: _anyBusy ? null : (twoFactor ? _submitCode : _submit),
            ),
            const SizedBox(height: 16),
            if (twoFactor)
              Center(
                child: TextButton(
                  onPressed: _anyBusy ? null : _leaveTwoFactor,
                  child: const Text(
                    'Back to sign in',
                    style: TextStyle(color: Aurora.textMuted, fontSize: 13),
                  ),
                ),
              )
            else
              ..._alternatives(),
          ],
        ),
      ),
    );
  }

  List<Widget> _credentialsStep() => [
        TextField(
          controller: _emailCtl,
          keyboardType: TextInputType.emailAddress,
          textInputAction: TextInputAction.next,
          autocorrect: false,
          autofillHints: const [AutofillHints.username],
          // Left enabled while busy: disabling a focused field drops the
          // keyboard, so a failed sign-in would fight the user's retry.
          readOnly: _anyBusy,
          onChanged: (_) => _clearMessages(),
          onSubmitted: (_) => _passwordFocus.requestFocus(),
          decoration: const InputDecoration(
            labelText: 'Email address',
            hintText: 'you@example.com',
            prefixIcon: Icon(Icons.mail_outline_rounded, size: 20),
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _passwordCtl,
          focusNode: _passwordFocus,
          obscureText: !_showPw,
          autocorrect: false,
          enableSuggestions: false,
          autofillHints: const [AutofillHints.password],
          textInputAction: TextInputAction.done,
          readOnly: _anyBusy,
          onChanged: (_) => _clearMessages(),
          onSubmitted: (_) => _anyBusy ? null : _submit(),
          decoration: InputDecoration(
            labelText: 'Password',
            prefixIcon: const Icon(Icons.lock_outline_rounded, size: 20),
            border: const OutlineInputBorder(),
            suffixIcon: IconButton(
              tooltip: _showPw ? 'Hide password' : 'Show password',
              icon: Icon(
                _showPw
                    ? Icons.visibility_off_rounded
                    : Icons.visibility_rounded,
                color: Aurora.textMuted,
                size: 20,
              ),
              onPressed: () => setState(() => _showPw = !_showPw),
            ),
          ),
        ),
      ];

  List<Widget> _codeStep() => [
        TextField(
          controller: _codeCtl,
          keyboardType: TextInputType.text,
          autocorrect: false,
          enableSuggestions: false,
          autofocus: true,
          readOnly: _anyBusy,
          textAlign: TextAlign.center,
          style: const TextStyle(
            fontSize: 20,
            letterSpacing: 4,
            fontWeight: FontWeight.w600,
          ),
          onChanged: (_) => _clearMessages(),
          onSubmitted: (_) => _anyBusy ? null : _submitCode(),
          decoration: const InputDecoration(
            hintText: '123456',
            hintStyle: TextStyle(letterSpacing: 4, color: Aurora.textMuted),
            border: OutlineInputBorder(),
          ),
        ),
      ];

  List<Widget> _alternatives() {
    final cfg = ref.watch(configProvider);
    final googleReady =
        googleServerClientId != null && googleServerClientId!.isNotEmpty;

    return [
      // Hidden rather than shown-and-broken when this build has no client id:
      // a button that can only ever fail is worse than no button.
      if (googleReady) ...[
        const AuthDivider('or'),
        const SizedBox(height: 16),
        GoogleButton(
          busy: _googleBusy,
          onPressed: _anyBusy ? null : _google,
        ),
        const SizedBox(height: 18),
      ],
      // Wrap, not Row: at a large system font scale the two labels together
      // are wider than the screen, and a Row would just clip the link.
      Wrap(
        alignment: WrapAlignment.center,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          const Text(
            'New to Farry? ',
            style: TextStyle(color: Aurora.textMuted, fontSize: 13),
          ),
          GestureDetector(
            onTap: _anyBusy ? null : _openSignup,
            child: const Text(
              'Create an account',
              style: TextStyle(
                color: Aurora.mint,
                fontSize: 13,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
      const SizedBox(height: 26),
      Center(
        child: TextButton.icon(
          onPressed: _anyBusy ? null : () => _ServerSheet.open(context),
          icon: const Icon(Icons.dns_rounded, size: 15, color: Aurora.textMuted),
          label: Text(
            '${cfg.host}:${cfg.port}',
            style: const TextStyle(color: Aurora.textMuted, fontSize: 12),
          ),
        ),
      ),
    ];
  }
}

/// Bottom sheet to point the app at the right backend before signing in.
/// Saves straight to [ConfigStore]: the LiveNotifier, which normally persists
/// config changes, isn't alive until after the auth gate opens.
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
