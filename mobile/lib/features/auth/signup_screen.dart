import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../state/auth.dart';
import 'widgets/auth_bits.dart';
import 'widgets/auth_scaffold.dart';

/// Create-account. On success the auth gate (watching `authProvider`) swaps
/// to the home screen, so this screen just pops itself back off.
///
/// [open] resolves to a non-null notice when the account was created but the
/// automatic sign-in didn't happen — the caller shows it, because the user's
/// next step is signing in, not signing up again.
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
  final _firstCtl = TextEditingController();
  final _lastCtl = TextEditingController();
  final _emailCtl = TextEditingController();
  final _passwordCtl = TextEditingController();
  final _lastFocus = FocusNode();
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
    _firstCtl.dispose();
    _lastCtl.dispose();
    _emailCtl.dispose();
    _passwordCtl.dispose();
    _lastFocus.dispose();
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
          // The backend keeps one display_name; the split fields are a UI
          // nicety, so rejoin them rather than inventing a schema for it.
          displayName: [_firstCtl.text.trim(), _lastCtl.text.trim()]
              .where((s) => s.isNotEmpty)
              .join(' '),
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
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        iconTheme: const IconThemeData(color: Aurora.authTextDim),
      ),
      child: AuthForm(
        children: [
          const Center(child: AuthLogo(size: 104)),
          const SizedBox(height: 18),
          const Text(
            'Full Name',
            style: TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w500,
              color: Aurora.authTextDim,
            ),
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: AuthField(
                  hint: 'First name',
                  controller: _firstCtl,
                  textCapitalization: TextCapitalization.words,
                  textInputAction: TextInputAction.next,
                  autofillHints: const [AutofillHints.givenName],
                  readOnly: _anyBusy,
                  onSubmitted: (_) => _lastFocus.requestFocus(),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: AuthField(
                  hint: 'Last name',
                  controller: _lastCtl,
                  focusNode: _lastFocus,
                  textCapitalization: TextCapitalization.words,
                  textInputAction: TextInputAction.next,
                  autofillHints: const [AutofillHints.familyName],
                  readOnly: _anyBusy,
                  onSubmitted: (_) => _emailFocus.requestFocus(),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          AuthField(
            label: 'Email',
            hint: 'Your email',
            controller: _emailCtl,
            focusNode: _emailFocus,
            keyboardType: TextInputType.emailAddress,
            textInputAction: TextInputAction.next,
            autocorrect: false,
            autofillHints: const [AutofillHints.newUsername],
            readOnly: _anyBusy,
            onChanged: (_) => setState(() => _error = null),
            onSubmitted: (_) => _passwordFocus.requestFocus(),
          ),
          const SizedBox(height: 14),
          AuthField(
            label: 'Password',
            hint: 'At least $_minPasswordLength characters',
            controller: _passwordCtl,
            focusNode: _passwordFocus,
            obscureText: !_showPw,
            autocorrect: false,
            enableSuggestions: false,
            autofillHints: const [AutofillHints.newPassword],
            textInputAction: TextInputAction.done,
            readOnly: _anyBusy,
            onChanged: (_) => setState(() => _error = null),
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
          if (_error != null) ...[
            const SizedBox(height: 14),
            AuthBanner.error(_error!),
          ],
          const SizedBox(height: 20),
          AuthCtaButton(
            label: 'Sign Up',
            loading: _busy,
            onPressed: _anyBusy ? null : _submit,
          ),
          if (googleReady) ...[
            const AuthDivider(),
            GoogleButton(
              busy: _googleBusy,
              onPressed: _anyBusy ? null : _google,
            ),
          ],
          const SizedBox(height: 22),
          Wrap(
            alignment: WrapAlignment.center,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              const Text(
                'Already have an account? ',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: Aurora.authTextDim,
                ),
              ),
              GestureDetector(
                onTap: _anyBusy ? null : () => Navigator.of(context).pop(),
                child: const Text(
                  'Sign In',
                  style: TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    color: Aurora.neon,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
