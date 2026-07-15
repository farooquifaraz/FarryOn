import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/auth.dart';
import 'widgets/auth_bits.dart';
import 'widgets/auth_scaffold.dart';

/// Create-account, pushed from [LoginScreen]. On success the auth gate
/// (watching `authProvider`) swaps to the home screen, so this screen just
/// pops itself back off.
///
/// [open] resolves to a non-null notice when the account was created but the
/// automatic sign-in didn't happen — the login screen shows it, because the
/// user's next step is signing in, not signing up again.
class SignupScreen extends ConsumerStatefulWidget {
  const SignupScreen({super.key});

  static Future<String?> open(BuildContext context) =>
      Navigator.of(context).push(
        MaterialPageRoute<String>(builder: (_) => const SignupScreen()),
      );

  @override
  ConsumerState<SignupScreen> createState() => _SignupScreenState();
}

class _SignupScreenState extends ConsumerState<SignupScreen> {
  final _nameCtl = TextEditingController();
  final _emailCtl = TextEditingController();
  final _passwordCtl = TextEditingController();
  final _emailFocus = FocusNode();
  final _passwordFocus = FocusNode();

  bool _showPw = false;
  bool _busy = false;
  bool _googleBusy = false;
  String? _error;

  bool get _anyBusy => _busy || _googleBusy;

  /// Mirrors the backend's rule (RegisterRequest: min_length=8) so the user
  /// hears about it while typing instead of after a round-trip.
  static const _minPasswordLength = 8;

  @override
  void dispose() {
    _nameCtl.dispose();
    _emailCtl.dispose();
    _passwordCtl.dispose();
    _emailFocus.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _emailCtl.text.trim();
    final password = _passwordCtl.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = 'Enter an email and a password.');
      return;
    }
    if (password.length < _minPasswordLength) {
      setState(() =>
          _error = 'Password must be at least $_minPasswordLength characters.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await ref.read(authProvider.notifier).signUp(
          email: email,
          password: password,
          displayName: _nameCtl.text.trim(),
        );
    if (!mounted) return;
    if (result.tokens != null) {
      Navigator.of(context).pop();
      return;
    }
    if (result.accountCreated) {
      // The account exists; only the auto sign-in failed. Send the user back
      // to sign-in with the reason, rather than claiming sign-up failed (a
      // retry would hit "email already exists" on their own account).
      Navigator.of(context).pop(
        result.message.isNotEmpty
            ? 'Account created. ${result.message}'
            : 'Account created — sign in to continue.',
      );
      return;
    }
    setState(() {
      _busy = false;
      _error = result.message.isNotEmpty
          ? result.message
          : "Couldn't create the account. Try again.";
    });
  }

  Future<void> _google() async {
    setState(() {
      _googleBusy = true;
      _error = null;
    });
    final result = await ref.read(authProvider.notifier).signInWithGoogle();
    if (!mounted) return;
    if (result.tokens != null) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      _googleBusy = false;
      if (!result.cancelled) {
        _error = result.message.isNotEmpty
            ? result.message
            : "Google sign-in didn't work. Try again.";
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final googleReady =
        googleServerClientId != null && googleServerClientId!.isNotEmpty;

    return AuthScaffold(
      appBar: AppBar(backgroundColor: Colors.transparent),
      child: AuthForm(
        children: [
          const AuthBrand(),
          const SizedBox(height: 30),
          const Text(
            'Create your account',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Aurora.textPrimary,
              fontSize: 27,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.5,
            ),
          ),
          const SizedBox(height: 7),
          const Text(
            'One account for your notes, reminders, and glasses.',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Aurora.textMuted,
              fontSize: 13.5,
              height: 1.45,
            ),
          ),
          const SizedBox(height: 28),
          // Google first: it's one tap and skips every field below.
          if (googleReady) ...[
            GoogleButton(
              busy: _googleBusy,
              onPressed: _anyBusy ? null : _google,
            ),
            const SizedBox(height: 18),
            const AuthDivider('or sign up with email'),
            const SizedBox(height: 18),
          ],
          TextField(
            controller: _nameCtl,
            textCapitalization: TextCapitalization.words,
            textInputAction: TextInputAction.next,
            readOnly: _anyBusy,
            autofillHints: const [AutofillHints.name],
            onSubmitted: (_) => _emailFocus.requestFocus(),
            decoration: const InputDecoration(
              labelText: 'Name (optional)',
              prefixIcon: Icon(Icons.person_outline_rounded, size: 20),
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _emailCtl,
            focusNode: _emailFocus,
            keyboardType: TextInputType.emailAddress,
            textInputAction: TextInputAction.next,
            autocorrect: false,
            readOnly: _anyBusy,
            autofillHints: const [AutofillHints.newUsername],
            onChanged: (_) => setState(() => _error = null),
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
            readOnly: _anyBusy,
            autofillHints: const [AutofillHints.newPassword],
            textInputAction: TextInputAction.done,
            onChanged: (_) => setState(() => _error = null),
            onSubmitted: (_) => _anyBusy ? null : _submit(),
            decoration: InputDecoration(
              labelText: 'Password',
              helperText: 'At least $_minPasswordLength characters',
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
          if (_error != null) ...[
            const SizedBox(height: 14),
            AuthBanner.error(_error!),
          ],
          const SizedBox(height: 22),
          GradientButton(
            label: _busy ? 'Creating account…' : 'Create account',
            icon: Icons.person_add_alt_1_rounded,
            onPressed: _anyBusy ? null : _submit,
          ),
        ],
      ),
    );
  }
}
