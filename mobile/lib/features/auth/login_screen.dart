import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config_store.dart';
import '../../core/theme.dart';
import '../../state/auth.dart';
import '../../state/providers.dart';
import 'signup_screen.dart';
import 'widgets/auth_bits.dart';
import 'widgets/auth_scaffold.dart';

/// Sign-in. Two ways in — Google, or email + password — plus the 2FA code
/// step when the account has it turned on.
///
/// The server address sits at the bottom because it's a developer control,
/// but it has to be reachable: nothing here can work while the app points at
/// the wrong backend, and Settings is behind the sign-in it's blocking.
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
      // On success FarryOnApp's auth listener pops this screen — the gate alone
      // can't, since this route sits *above* the home it swaps.
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
    final cfg = ref.watch(configProvider);
    final googleReady =
        googleServerClientId != null && googleServerClientId!.isNotEmpty;

    return AuthScaffold(
      // During the 2FA step, system-back returns to the credentials form
      // rather than dropping the user out mid-sign-in.
      child: PopScope(
        canPop: !twoFactor,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop && twoFactor) _leaveTwoFactor();
        },
        child: AuthForm(
          children: [
            const Center(child: AuthLogo(size: 108)),
            const SizedBox(height: 18),
            if (twoFactor) ...[
              const Text(
                'Two-factor check',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w700,
                  color: Colors.white,
                ),
              ),
              const SizedBox(height: 6),
              const Text(
                'Enter the 6-digit code from your authenticator app, or one of your recovery codes.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 12.5,
                  height: 1.45,
                  color: Aurora.authTextDim,
                ),
              ),
              const SizedBox(height: 20),
              AuthField(
                label: 'Code',
                hint: '123456',
                controller: _codeCtl,
                autofocus: true,
                autocorrect: false,
                enableSuggestions: false,
                readOnly: _anyBusy,
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontSize: 19,
                  letterSpacing: 4,
                  fontWeight: FontWeight.w600,
                  color: Colors.white,
                ),
                onChanged: (_) => _clearMessages(),
                onSubmitted: (_) => _anyBusy ? null : _submitCode(),
              ),
            ] else ...[
              AuthField(
                label: 'Email',
                hint: 'Your email',
                controller: _emailCtl,
                keyboardType: TextInputType.emailAddress,
                textInputAction: TextInputAction.next,
                autocorrect: false,
                autofillHints: const [AutofillHints.username],
                readOnly: _anyBusy,
                onChanged: (_) => _clearMessages(),
                onSubmitted: (_) => _passwordFocus.requestFocus(),
              ),
              const SizedBox(height: 14),
              AuthField(
                label: 'Password',
                hint: 'Password',
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
                suffix: IconButton(
                  tooltip: _showPw ? 'Hide password' : 'Show password',
                  icon: Icon(
                    _showPw
                        ? Icons.visibility_off_rounded
                        : Icons.visibility_rounded,
                    size: 19,
                    color: Aurora.authTextFaint,
                  ),
                  onPressed: () => setState(() => _showPw = !_showPw),
                ),
              ),
            ],
            if (_notice != null) ...[
              const SizedBox(height: 14),
              AuthBanner.success(_notice!),
            ],
            if (_error != null) ...[
              const SizedBox(height: 14),
              AuthBanner.error(_error!),
            ],
            const SizedBox(height: 20),
            AuthCtaButton(
              label: twoFactor ? 'Verify Code' : 'Sign In',
              loading: _busy,
              onPressed: _anyBusy ? null : (twoFactor ? _submitCode : _submit),
            ),
            if (twoFactor)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Center(
                  child: TextButton(
                    onPressed: _anyBusy ? null : _leaveTwoFactor,
                    child: const Text(
                      'Back to sign in',
                      style:
                          TextStyle(color: Aurora.authTextDim, fontSize: 12.5),
                    ),
                  ),
                ),
              )
            else ...[
              // Hidden rather than shown-and-broken when this build has no
              // client id: a button that can only ever fail is worse than no
              // button.
              if (googleReady) ...[
                const AuthDivider(),
                GoogleButton(
                  busy: _googleBusy,
                  onPressed: _anyBusy ? null : _google,
                ),
              ],
              const SizedBox(height: 22),
              // Wrap, not Row: at a large system font scale the two labels
              // together are wider than the screen, and a Row would clip the
              // link.
              Wrap(
                alignment: WrapAlignment.center,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text(
                    "Don't have an account? ",
                    style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                      color: Aurora.authTextDim,
                    ),
                  ),
                  GestureDetector(
                    onTap: _anyBusy ? null : _openSignup,
                    child: const Text(
                      'Sign Up',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                        color: Aurora.neon,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Center(
                child: TextButton.icon(
                  onPressed: _anyBusy ? null : () => _ServerSheet.open(context),
                  icon: const Icon(Icons.dns_rounded,
                      size: 14, color: Aurora.authTextFaint),
                  label: Text(
                    '${cfg.host}:${cfg.port}',
                    style: const TextStyle(
                        color: Aurora.authTextFaint, fontSize: 11.5),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
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
          16, 18, 16, 18 + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'SERVER ADDRESS',
            style: TextStyle(
              color: Aurora.mint,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 10),
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
          AuthCtaButton(label: 'Save', onPressed: _save),
        ],
      ),
    );
  }
}
