import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../data/live_client.dart' show ConnectionStatus;
import '../../state/providers.dart';
import 'translate_controller.dart';
import 'translate_providers.dart';
import 'translate_state.dart';

/// Live translation: hear a room in one language, read and hear it in yours.
///
/// Opening this screen **disconnects the assistant** and closing it reconnects.
/// That is not a limitation to hide — only one feature can own the microphone,
/// and a user who is not told will read Farry's silence as a bug. So the
/// consequence is stated before anything starts, and the return is confirmed.
class TranslateScreen extends ConsumerStatefulWidget {
  const TranslateScreen({super.key});

  static Future<void> open(BuildContext context) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => const TranslateScreen(),
      ));

  @override
  ConsumerState<TranslateScreen> createState() => _TranslateScreenState();
}

class _TranslateScreenState extends ConsumerState<TranslateScreen> {
  Timer? _clock;
  bool _wasLiveConnected = false;

  @override
  void initState() {
    super.initState();
    // The elapsed label is derived from startedAt; this only forces a repaint.
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && ref.read(translateProvider).isRunning) setState(() {});
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _takeOverTheMic());
  }

  Future<void> _takeOverTheMic() async {
    final live = ref.read(liveProvider);
    _wasLiveConnected = live.connection != ConnectionStatus.disconnected;
    if (_wasLiveConnected) {
      await ref.read(liveProvider.notifier).disconnect();
    }
    ref
        .read(translateControllerProvider)
        .primeFromConfig(ref.read(configProvider));
  }

  @override
  void dispose() {
    _clock?.cancel();
    // Hand the microphone back. Read the controller off the container rather
    // than `ref` — this runs during teardown, when `ref.read` is no longer
    // safe, and leaving the assistant disconnected would look like a crash.
    unawaited(_restore());
    super.dispose();
  }

  Future<void> _restore() async {
    await _controller.stop();
    if (_wasLiveConnected) await _liveNotifier.connect();
  }

  late final TranslateController _controller =
      ref.read(translateControllerProvider);
  late final LiveNotifier _liveNotifier = ref.read(liveProvider.notifier);

  Future<void> _toggle() async {
    final state = ref.read(translateProvider);
    if (state.isRunning) {
      await _controller.stop();
      return;
    }
    if (state.targetLanguage.isEmpty) {
      final picked = await _pickLanguage();
      if (picked == null) return;
    }
    await _controller.start();
  }

  Future<String?> _pickLanguage() async {
    final current = ref.read(translateProvider).targetLanguage;
    final picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Aurora.surfaceHigh,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _LanguageSheet(selected: current),
    );
    if (picked == null) return null;
    await _controller.setTargetLanguage(picked);
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(translateTargetLanguage: picked);
    return picked;
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(translateProvider);
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        backgroundColor: Aurora.base,
        elevation: 0,
        title: const Text('Live translation',
            style: TextStyle(color: Aurora.textPrimary, fontSize: 17)),
        iconTheme: const IconThemeData(color: Aurora.textPrimary),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: TextButton(
              onPressed: _pickLanguage,
              child: Text(
                s.targetLanguage.isEmpty
                    ? 'Choose language'
                    : '→ ${translateLanguageName(s.targetLanguage)}',
                style: const TextStyle(color: Aurora.mint, fontSize: 13),
              ),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            if (!s.isRunning && s.turns.isEmpty) const _FarryPausedNotice(),
            if (s.status == TranslateStatus.reconnecting) const _ReconnectBar(),
            if (s.error != null) _ErrorBar(s.error!),
            Expanded(
              child: s.turns.isEmpty
                  ? _EmptyState(running: s.isRunning)
                  : _TurnList(turns: s.turns, target: s.targetLanguage),
            ),
            _Controls(
              state: s,
              onToggle: _toggle,
              onCaptionsOnly: (v) {
                _controller.setCaptionsOnly(v);
                final cfg = ref.read(configProvider);
                ref.read(configProvider.notifier).state =
                    cfg.copyWith(translateCaptionsOnly: v);
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _FarryPausedNotice extends StatelessWidget {
  const _FarryPausedNotice();

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.purple, 0.12),
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.info_outline, size: 16, color: Aurora.purpleSoft),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                'Farry stays quiet while you translate. She comes back on her '
                'own when you leave this screen.',
                style: TextStyle(
                    color: Aurora.textMuted, fontSize: 12, height: 1.4),
              ),
            ),
          ],
        ),
      );
}

class _ReconnectBar extends StatelessWidget {
  const _ReconnectBar();

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.amber, 0.14),
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Text(
          'Reconnecting… what you already have is kept.',
          style: TextStyle(color: Aurora.amber, fontSize: 12),
        ),
      );
}

class _ErrorBar extends StatelessWidget {
  const _ErrorBar(this.message);
  final String message;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.danger, 0.14),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(message,
            style: const TextStyle(color: Aurora.danger, fontSize: 12)),
      );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.running});
  final bool running;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 36),
          child: Text(
            running
                ? 'Listening…'
                : 'Point the microphone at whoever is speaking. Their language '
                    'is detected on its own — you only pick the one you want '
                    'to hear.',
            textAlign: TextAlign.center,
            style: const TextStyle(
                color: Aurora.textMuted, fontSize: 13, height: 1.5),
          ),
        ),
      );
}

class _TurnList extends StatelessWidget {
  const _TurnList({required this.turns, required this.target});
  final List<TranslateTurn> turns;
  final String target;

  @override
  Widget build(BuildContext context) => ListView.builder(
        reverse: true,
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
        itemCount: turns.length,
        itemBuilder: (_, i) => _TurnTile(
          turn: turns[turns.length - 1 - i],
          target: target,
        ),
      );
}

class _TurnTile extends StatelessWidget {
  const _TurnTile({required this.turn, required this.target});
  final TranslateTurn turn;
  final String target;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: Aurora.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _label('Heard${turn.heardLang != null ? ' · '
              '${translateLanguageName(turn.heardLang!)}' : ''}'),
          const SizedBox(height: 4),
          Text(
            turn.heard,
            style: TextStyle(
              // Muted until the sentence is finalised, so the user can see the
              // difference between "still hearing this" and "this is settled".
              color: turn.heardFinal ? Aurora.textPrimary : Aurora.textMuted,
              fontSize: 14,
              height: 1.45,
            ),
          ),
          if (turn.sameLanguage) ...[
            const SizedBox(height: 10),
            _SameLanguageNote(target: target),
          ] else if (turn.translated.isNotEmpty) ...[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.only(left: 10),
              decoration: const BoxDecoration(
                border: Border(
                    left: BorderSide(color: Aurora.mint, width: 2)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _label(translateLanguageName(target)),
                  const SizedBox(height: 4),
                  Text(
                    turn.translated,
                    style: const TextStyle(
                        color: Aurora.textPrimary, fontSize: 15, height: 1.45),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  static Widget _label(String text) => Text(
        text.toUpperCase(),
        style: const TextStyle(
          color: Aurora.textMuted,
          fontSize: 10,
          letterSpacing: 0.7,
        ),
      );
}

/// The explanation that keeps a deliberate silence from reading as a fault.
class _SameLanguageNote extends StatelessWidget {
  const _SameLanguageNote({required this.target});
  final String target;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(9),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.amber, 0.10),
          borderRadius: BorderRadius.circular(9),
        ),
        child: Text(
          'Already in ${translateLanguageName(target)} — nothing to translate, '
          'so it was not spoken again.',
          style: const TextStyle(
              color: Aurora.amber, fontSize: 11.5, height: 1.4),
        ),
      );
}

class _Controls extends StatelessWidget {
  const _Controls({
    required this.state,
    required this.onToggle,
    required this.onCaptionsOnly,
  });

  final TranslateState state;
  final Future<void> Function() onToggle;
  final ValueChanged<bool> onCaptionsOnly;

  @override
  Widget build(BuildContext context) {
    final running = state.isRunning;
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: Aurora.glassBorder)),
      ),
      child: Column(
        children: [
          Row(
            children: [
              GestureDetector(
                onTap: onToggle,
                child: Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: running ? Aurora.teal : Colors.transparent,
                    border: Border.all(
                        color: running ? Aurora.teal : Aurora.glassBorder),
                  ),
                  child: Icon(
                    running ? Icons.stop_rounded : Icons.mic_none_rounded,
                    color: running ? Aurora.tealInk : Aurora.mint,
                    size: 24,
                  ),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      switch (state.status) {
                        TranslateStatus.listening => 'Listening…',
                        TranslateStatus.starting => 'Starting…',
                        TranslateStatus.reconnecting => 'Reconnecting…',
                        _ => 'Tap to start',
                      },
                      style: const TextStyle(
                          color: Aurora.textPrimary, fontSize: 14),
                    ),
                    if (running)
                      Text(state.elapsedLabel,
                          style: const TextStyle(
                              color: Aurora.textMuted, fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Text only, no voice',
                  style: TextStyle(color: Aurora.textMuted, fontSize: 12),
                ),
              ),
              Switch(
                value: state.captionsOnly,
                activeThumbColor: Aurora.tealInk,
                activeTrackColor: Aurora.teal,
                onChanged: onCaptionsOnly,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _LanguageSheet extends StatelessWidget {
  const _LanguageSheet({required this.selected});
  final String selected;

  @override
  Widget build(BuildContext context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 18, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Which language do you want to hear?',
                  style:
                      TextStyle(color: Aurora.textPrimary, fontSize: 15)),
              const SizedBox(height: 6),
              const Text(
                "The speaker's language is detected on its own — you never "
                'have to say what it is.',
                style: TextStyle(
                    color: Aurora.textMuted, fontSize: 12, height: 1.4),
              ),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final (code, name) in kTranslateLanguages)
                    GestureDetector(
                      onTap: () => Navigator.pop(context, code),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 8),
                        decoration: BoxDecoration(
                          color: code == selected
                              ? Aurora.teal
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(
                            color: code == selected
                                ? Aurora.teal
                                : Aurora.glassBorder,
                          ),
                        ),
                        child: Text(
                          name,
                          style: TextStyle(
                            color: code == selected
                                ? Aurora.tealInk
                                : Aurora.textPrimary,
                            fontSize: 13,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      );
}
