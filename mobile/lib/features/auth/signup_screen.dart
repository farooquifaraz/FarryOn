import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../core/ui.dart';
import '../../state/auth.dart';

/// Create-account screen, pushed from [LoginScreen]. On success the auth
/// gate (watching [authProvider]) swaps to the home screen, so this screen
/// just pops itself back off.
///
/// [open] resolves to a non-null notice when the account was created but the
/// automatic sign-in didn't happen — the caller (the login screen) shows it,
/// because the user's next step is signing in, not signing up again.
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

  bool _showPw = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _nameCtl.dispose();
    _emailCtl.dispose();
    _passwordCtl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _emailCtl.text.trim();
    final password = _passwordCtl.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = 'Enter an email and a password.');
      return;
    }
    if (password.length < 8) {
      setState(() => _error = 'Password must be at least 8 characters.');
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
      // Signed in — pop back so the auth gate (now signedIn) shows home.
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(title: const Text('Create account')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
        children: [
          Container(
            padding: const EdgeInsets.fromLTRB(20, 22, 20, 22),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF0F6E56), Color(0xFF1D9E75), Color(0xFF534AB7)],
              ),
              borderRadius: BorderRadius.circular(16),
            ),
            child: const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Join Farry',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.w600)),
                SizedBox(height: 4),
                Text('One account for your notes, reminders, and glasses.',
                    style: TextStyle(
                        color: Color(0xFFD6F3E9), fontSize: 12, height: 1.4)),
              ],
            ),
          ),
          const SizedBox(height: 22),
          const SectionLabel('Your details'),
          const SizedBox(height: 2),
          TextField(
            controller: _nameCtl,
            textCapitalization: TextCapitalization.words,
            enabled: !_busy,
            decoration: const InputDecoration(
              labelText: 'Name (optional)',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
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
              helperText: 'At least 8 characters',
              border: const OutlineInputBorder(),
              suffixIcon: IconButton(
                icon: Icon(_showPw ? Icons.visibility_off : Icons.visibility,
                    color: Aurora.textMuted),
                onPressed: () => setState(() => _showPw = !_showPw),
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Container(
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
                    child: Text(_error!,
                        style: const TextStyle(
                            color: Aurora.danger, fontSize: 12.5, height: 1.4)),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 18),
          GradientButton(
            label: _busy ? 'Creating account…' : 'Create account',
            icon: Icons.person_add_alt_1_rounded,
            onPressed: _busy ? null : _submit,
          ),
        ],
      ),
    );
  }
}
