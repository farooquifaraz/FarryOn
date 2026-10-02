import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../../capture/device_registry.dart';
import '../../core/config.dart';
import '../../core/theme.dart';
import '../../data/live_client.dart' show ConnectionStatus;
import '../../playback/device_voice.dart';
import '../../state/providers.dart';
import 'translate_controller.dart';
import 'translate_language_picker.dart';
import 'translate_languages.dart';
import 'translate_providers.dart';
import 'translate_state.dart';
import 'translate_transcript.dart';

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

  /// A save is in flight — the button waits rather than firing twice.
  bool _saving = false;

  // Captured EAGERLY in initState, never with a lazy `late` initializer.
  //
  // `dispose()` is where the assistant gets handed back, and by then `ref` is
  // dead — a `late final x = ref.read(...)` field resolves on FIRST ACCESS,
  // which for these two is inside dispose. That threw, `_restore()` died before
  // reconnecting, and the user was left staring at "Session ended" under a
  // banner promising Farry would come back on her own.
  late final TranslateController _controller;
  late final LiveNotifier _liveNotifier;

  @override
  void initState() {
    super.initState();
    _controller = ref.read(translateControllerProvider);
    _liveNotifier = ref.read(liveProvider.notifier);
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
    ref.read(translateControllerProvider).primeFromConfig(
          ref.read(configProvider),
          glassesConnected: live.glassesConnected,
        );
  }

  @override
  void dispose() {
    _clock?.cancel();
    unawaited(_restore());
    super.dispose();
  }

  /// Hand the microphone back and bring Farry with it.
  ///
  /// Nothing here may throw: this runs detached during teardown, so an
  /// exception has no one to catch it and simply leaves the assistant off.
  Future<void> _restore() async {
    try {
      await _controller.stop();
    } catch (_) {
      // Even a failed stop must not block the reconnect below — an assistant
      // that never comes back is the worse of the two failures.
    }
    if (_wasLiveConnected) {
      try {
        await _liveNotifier.connect();
      } catch (_) {
        // The live screen's own reconnect overlay covers this.
      }
    }
  }

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

  /// A typed sentence: the language is asked for first if none is chosen.
  Future<void> _sendTyped(String text) async {
    if (ref.read(translateProvider).targetLanguage.isEmpty) {
      final picked = await _pickLanguage();
      if (picked == null) return;
    }
    await _controller.sendText(text);
  }

  Future<String?> _pickLanguage() async {
    final voice = DeviceVoice();
    final picked = await TranslateLanguagePicker.open(
      context,
      ref.read(translateProvider).targetLanguage,
      installedVoices: voice.installedLanguages,
      onAddVoices: voice.openVoiceInstaller,
    );
    if (picked == null || !mounted) return null;
    await _controller.setTargetLanguage(picked);
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(translateTargetLanguage: picked);
    return picked;
  }

  /// Keep the conversation so far, as a note in Your stuff.
  ///
  /// Not automatic and not on exit: the transcript belongs to whoever was
  /// speaking, and saving it is a decision. Failures are reported inline —
  /// telling someone their conversation was saved when it was not is worse
  /// than telling them it failed.
  Future<void> _save() async {
    final s = ref.read(translateProvider);
    final body = renderTranslationNote(
      turns: s.turns,
      targetLanguage: s.targetLanguage,
      at: DateTime.now(),
    );
    setState(() => _saving = true);
    String? problem;
    try {
      await ref.read(dataApiProvider).createNote(body);
    } catch (e) {
      problem = 'Could not save it — $e';
    }
    if (!mounted) return;
    setState(() => _saving = false);
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(problem ?? 'Saved to your notes.'),
      ));
  }

  /// Hand the transcript to whatever the user shares with — WhatsApp, mail,
  /// a notes app. The same text a saved note holds, so what is sent reads the
  /// same as what is kept.
  Future<void> _share() async {
    final s = ref.read(translateProvider);
    final body = renderTranslationNote(
      turns: s.turns,
      targetLanguage: s.targetLanguage,
      at: DateTime.now(),
    );
    try {
      await Share.share(body, subject: 'Live translation');
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text('Could not share it — $e')));
    }
  }

  /// Empty the transcript, after asking. It cannot be brought back, and the
  /// button sits next to Save.
  Future<void> _clear() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Aurora.surface,
        title: const Text('Clear this translation?',
            style: TextStyle(color: Aurora.textPrimary, fontSize: 17)),
        content: const Text(
          'Everything on this screen is removed. Notes you already saved are '
          'kept, and listening carries on if it is running.',
          style: TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Keep', style: TextStyle(color: Aurora.textMuted)),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Clear', style: TextStyle(color: Aurora.danger)),
          ),
        ],
      ),
    );
    if (sure == true) _controller.clearTranscript();
  }

  /// The microphone for translation — this screen's own, see
  /// `TranslateController.setMic`. Remembered, and the assistant's is not
  /// touched.
  Future<void> _setMic(CaptureDeviceKind kind) async {
    await _controller.setMic(kind);
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(translateMic: kind.name);
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<bool>(liveProvider.select((l) => l.glassesConnected),
        (_, connected) => unawaited(_controller.setGlassesConnected(connected)));
    // Keep the session's socket on a live token. Without this the config
    // snapshot taken when the screen opened is the only one it ever sees, and
    // fifteen minutes later every reconnect is refused.
    ref.listen<AppConfig>(configProvider, (_, next) {
      _controller.updateConfig(next);
    });
    final s = ref.watch(translateProvider);
    // Watched, not read: plugging the glasses in should light the screen up
    // without the user having to back out and come in again.
    final glassesOn =
        ref.watch(liveProvider.select((l) => l.glassesConnected));
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        backgroundColor: Aurora.base,
        elevation: 0,
        title: const Text('Live translation',
            style: TextStyle(color: Aurora.textPrimary, fontSize: 17)),
        iconTheme: const IconThemeData(color: Aurora.textPrimary),
        actions: [
          // Saving is deliberately a tap, never automatic. A translator is
          // pointed at other people's conversations; keeping one is the user's
          // decision to make, once, and not the default.
          if (hasSomethingToSave(s.turns))
            IconButton(
              onPressed: _saving ? null : _save,
              icon: _saving
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Aurora.mint),
                    )
                  : const Icon(Icons.bookmark_add_outlined),
              color: Aurora.mint,
              tooltip: 'Save this translation',
            ),
          // Share and Clear live behind one button: the bar already holds the
          // title, Save and the language, and a fourth and fifth icon push
          // the language off a narrow phone.
          if (s.turns.isNotEmpty)
            PopupMenuButton<String>(
              tooltip: 'More',
              icon: const Icon(Icons.more_vert, color: Aurora.textPrimary),
              color: Aurora.surface,
              onSelected: (v) => v == 'share' ? _share() : _clear(),
              itemBuilder: (_) => [
                if (hasSomethingToSave(s.turns))
                  const PopupMenuItem(
                    value: 'share',
                    child: _MenuRow(Icons.ios_share, 'Share…'),
                  ),
                const PopupMenuItem(
                  value: 'clear',
                  child: _MenuRow(Icons.delete_sweep_outlined, 'Clear'),
                ),
              ],
            ),
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
            if (!glassesOn) const _PhoneMicPanel(),
            if (glassesOn && !s.isRunning && s.turns.isEmpty)
              const _FarryPausedNotice(),
            if (s.status == TranslateStatus.reconnecting) const _ReconnectBar(),
            if (s.error != null) _ErrorBar(s.error!),
            if (s.notice != null) _NoticeBar(s.notice!),
            Expanded(
              child: s.turns.isEmpty
                  ? _EmptyState(running: s.isRunning, typing: s.typing)
                  : _TurnList(turns: s.turns, target: s.targetLanguage),
            ),
            // No glasses: type instead. The microphone stays closed, so the
            // spoken translation cannot loop back into it.
            if (!glassesOn) _TypeBar(onSend: _sendTyped),
            _Controls(
              state: s,
              // The phone's microphone listens when the glasses are off.
              enabled: true,
              // A choice only exists while the glasses are there to choose.
              onMic: glassesOn ? _setMic : null,
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

class _MenuRow extends StatelessWidget {
  const _MenuRow(this.icon, this.label);
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Icon(icon, size: 18, color: Aurora.textPrimary),
          const SizedBox(width: 10),
          Text(label,
              style: const TextStyle(color: Aurora.textPrimary, fontSize: 14)),
        ],
      );
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

/// A heads-up, not a failure.
///
/// Deliberately a different colour from [_ErrorBar]: "10 minutes of
/// translation left" and "translation is unavailable" are not the same news,
/// and painting them alike teaches people to skip both.
class _NoticeBar extends StatelessWidget {
  const _NoticeBar(this.message);
  final String message;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.amber, 0.12),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(message,
            style: const TextStyle(color: Aurora.amber, fontSize: 12)),
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
  const _EmptyState({required this.running, this.typing = false});
  final bool running;
  final bool typing;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 36),
          child: Text(
            typing
                ? 'Type a sentence below. Its language is detected on its '
                    'own — you only pick the one you want it in.'
                : running
                    ? 'Listening…'
                    : 'Point the microphone at whoever is speaking. Their '
                        'language is detected on its own — you only pick the '
                        'one you want to hear.',
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
    // The language THIS card was translated into: the screen's target can be
    // changed mid-session, and an earlier card keeps the one it was made in.
    final target = turn.targetLang ?? this.target;
    final heardRtl = isRtlLanguage(turn.heardLang);
    final targetRtl = isRtlLanguage(target);
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
          _label(
            'Heard${turn.heardLang != null ? ' · '
                '${translateLanguageName(turn.heardLang!)}' : ''}',
            Aurora.purpleSoft,
          ),
          const SizedBox(height: 4),
          // What was SAID, in the speaker's own words — a reference, kept
          // visually quieter than the translation so the eye lands on the
          // thing the user actually came for.
          _DirectionalText(
            turn.heard,
            rtl: heardRtl,
            style: TextStyle(
              // Dimmer still until the sentence is finalised, so "still
              // hearing this" and "this is settled" look different.
              color: turn.heardFinal
                  ? Aurora.purpleSoft
                  : Aurora.purpleSoft.withValues(alpha: 0.55),
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
                  _label(translateLanguageName(target), Aurora.mint),
                  const SizedBox(height: 4),
                  // The translation: brighter, larger, and the only white text
                  // in the card.
                  _DirectionalText(
                    turn.translated,
                    rtl: targetRtl,
                    style: const TextStyle(
                        color: Aurora.textPrimary,
                        fontSize: 15.5,
                        height: 1.5),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  static Widget _label(String text, Color color) => Text(
        text.toUpperCase(),
        style: TextStyle(
          color: color,
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
    required this.enabled,
    required this.onToggle,
    required this.onCaptionsOnly,
    this.onMic,
  });

  final TranslateState state;
  final bool enabled;

  /// Choose the microphone, or null when there is no choice to make.
  final Future<void> Function(CaptureDeviceKind kind)? onMic;
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
                onTap: enabled ? onToggle : null,
                child: Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: running ? Aurora.teal : Colors.transparent,
                    border: Border.all(
                        color: running
                            ? Aurora.teal
                            : (enabled
                                ? Aurora.glassBorder
                                : Aurora.glassBorder.withValues(alpha: 0.04))),
                  ),
                  child: Icon(
                    running ? Icons.stop_rounded : Icons.mic_none_rounded,
                    color: running
                        ? Aurora.tealInk
                        : (enabled ? Aurora.mint : Aurora.textMuted),
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
                        TranslateStatus.listening =>
                          state.typing ? 'Typing — nothing is heard' : 'Listening…',
                        TranslateStatus.starting => 'Starting…',
                        TranslateStatus.reconnecting => 'Reconnecting…',
                        _ => enabled ? 'Tap to start' : 'Not available',
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
          if (onMic != null && !state.typing) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                const Expanded(
                  child: Text(
                    'Microphone',
                    style: TextStyle(color: Aurora.textMuted, fontSize: 12),
                  ),
                ),
                for (final kind in CaptureDeviceKind.values) ...[
                  const SizedBox(width: 8),
                  ChoiceChip(
                    label: Text(
                        kind == CaptureDeviceKind.phone ? 'Phone' : 'Glasses'),
                    selected: state.mic == kind.name,
                    onSelected: (_) => onMic!(kind),
                    showCheckmark: false,
                    selectedColor: Aurora.teal,
                    backgroundColor: Colors.transparent,
                    side: const BorderSide(color: Aurora.glassBorder),
                    labelStyle: TextStyle(
                      color: state.mic == kind.name
                          ? Aurora.tealInk
                          : Aurora.textMuted,
                      fontSize: 12,
                    ),
                    visualDensity: VisualDensity.compact,
                  ),
                ],
              ],
            ),
          ],
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

/// Shown whenever the glasses are not connected: the phone's own microphone
/// listens instead (2026-09-28), and the panel says what that costs — on the
/// loudspeaker the microphone is held while a translation plays, so speech
/// during it is missed; earphones or "Text only" avoid that. Typing is the
/// other way in.
class _PhoneMicPanel extends StatelessWidget {
  const _PhoneMicPanel();

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.amber, 0.12),
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.visibility_outlined, size: 18, color: Aurora.amber),
            SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Glasses not connected — listening on the phone',
                    style: TextStyle(color: Aurora.amber, fontSize: 13.5),
                  ),
                  SizedBox(height: 4),
                  Text(
                    "The phone's microphone hears whoever speaks, in any "
                    'language, and the translation is shown below. On the '
                    'loudspeaker anything said while it plays is missed — '
                    'use earphones or "Text only". You can also type a '
                    'sentence below. With the glasses on, the translation '
                    'plays in your ear and nothing is missed.',
                    style: TextStyle(
                        color: Aurora.textMuted, fontSize: 12, height: 1.45),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}

/// The typed way in: a sentence and a Send button, shown only while the
/// glasses are off. Sending clears the field; the sentence comes back from
/// the server as a card, translated.
class _TypeBar extends StatefulWidget {
  const _TypeBar({required this.onSend});
  final Future<void> Function(String text) onSend;

  @override
  State<_TypeBar> createState() => _TypeBarState();
}

class _TypeBarState extends State<_TypeBar> {
  final _ctl = TextEditingController();
  bool _sending = false;

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _ctl.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await widget.onSend(text);
      _ctl.clear();
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _ctl,
                minLines: 1,
                maxLines: 3,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(),
                style: const TextStyle(color: Aurora.textPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'Type to translate…',
                  hintStyle: const TextStyle(color: Aurora.textMuted),
                  filled: true,
                  fillColor: Aurora.glass,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: const BorderSide(color: Aurora.glassBorder),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: const BorderSide(color: Aurora.glassBorder),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: _sending ? null : _send,
              tooltip: 'Send',
              style: IconButton.styleFrom(backgroundColor: Aurora.teal),
              icon: _sending
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Aurora.tealInk),
                    )
                  : const Icon(Icons.send_rounded, color: Aurora.tealInk),
            ),
          ],
        ),
      );
}

/// Text laid out in the direction its language is actually written.
///
/// Arabic, Urdu, Persian, Hebrew and Sindhi all read right-to-left. Left-
/// aligning them is what made a correct Urdu translation look broken — the
/// words were right, but the line began on the wrong side and the sentence
/// ended at the wrong end.
class _DirectionalText extends StatelessWidget {
  const _DirectionalText(this.text, {required this.rtl, required this.style});

  final String text;
  final bool rtl;
  final TextStyle style;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: double.infinity,
        child: Text(
          text,
          textDirection: rtl ? TextDirection.rtl : TextDirection.ltr,
          textAlign: rtl ? TextAlign.right : TextAlign.left,
          style: style,
        ),
      );
}
