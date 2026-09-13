import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../data/live_client.dart' show ConnectionStatus;
import '../../playback/device_voice.dart';
import '../../state/providers.dart';
import '../translate/translate_language_picker.dart';
import '../translate/translate_languages.dart';
import 'speak_for_me_controller.dart';

/// Type it; the phone — or the glasses, when they are on someone — says it.
///
/// Opening this screen **disconnects the assistant** and closing it reconnects,
/// for the same reason the translate screen does: Farry's microphone would
/// hear the phone's voice as the user speaking.
class SpeakForMeScreen extends ConsumerStatefulWidget {
  const SpeakForMeScreen({super.key});

  static Future<void> open(BuildContext context) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => const SpeakForMeScreen(),
      ));

  @override
  ConsumerState<SpeakForMeScreen> createState() => _SpeakForMeScreenState();
}

class _SpeakForMeScreenState extends ConsumerState<SpeakForMeScreen> {
  late final SpeakForMeController _controller;
  late final LiveNotifier _liveNotifier;
  late final DeviceVoice _voice;
  final _input = TextEditingController();
  final _focus = FocusNode();
  StreamSubscription<SpeakForMeState>? _sub;
  SpeakForMeState _state = const SpeakForMeState();
  bool _wasLiveConnected = false;

  @override
  void initState() {
    super.initState();
    // Captured eagerly — see the same note in translate_screen.dart: by the
    // time dispose() runs, `ref` is gone.
    _liveNotifier = ref.read(liveProvider.notifier);
    _voice = DeviceVoice();
    _controller = SpeakForMeController(
      language: defaultSpeakLanguage(ref.read(configProvider)),
      voice: _voice,
    );
    _state = _controller.state;
    _sub = _controller.stateStream.listen((s) {
      if (mounted) setState(() => _state = s);
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _quietFarry());
  }

  Future<void> _quietFarry() async {
    final live = ref.read(liveProvider);
    _wasLiveConnected = live.connection != ConnectionStatus.disconnected;
    if (_wasLiveConnected) {
      await ref.read(liveProvider.notifier).disconnect();
    }
    if (mounted) _focus.requestFocus();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _input.dispose();
    _focus.dispose();
    unawaited(_restore());
    super.dispose();
  }

  /// Nothing here may throw: it runs detached during teardown.
  Future<void> _restore() async {
    try {
      await _controller.dispose();
    } catch (_) {
      // A voice that would not stop must not keep Farry from coming back.
    }
    if (_wasLiveConnected) {
      try {
        await _liveNotifier.connect();
      } catch (_) {
        // The live screen's own reconnect overlay covers this.
      }
    }
  }

  Future<void> _speak([String? phrase]) async {
    final text = phrase ?? _input.text;
    if (text.trim().isEmpty) return;
    if (phrase == null) _input.clear();
    _focus.requestFocus();
    await _controller.say(text);
  }

  Future<void> _pickLanguage() async {
    final picked = await TranslateLanguagePicker.open(
      context,
      _state.language,
      installedVoices: _voice.installedLanguages,
      onAddVoices: _voice.openVoiceInstaller,
    );
    if (picked == null || !mounted) return;
    _controller.setLanguage(picked);
    final cfg = ref.read(configProvider);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(speakLanguage: picked);
  }

  /// Keep or drop a phrase on the quick row. Starter phrases cannot be
  /// unpinned — they are in code, not in the user's list.
  void _togglePinned(String text) {
    final cfg = ref.read(configProvider);
    final pinned = [...cfg.speakPhrases];
    final wasPinned = pinned.remove(text);
    if (!wasPinned) pinned.add(text);
    ref.read(configProvider.notifier).state =
        cfg.copyWith(speakPhrases: pinned);
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(wasPinned
            ? 'Removed from your quick phrases.'
            : 'Kept as a quick phrase.'),
      ));
  }

  @override
  Widget build(BuildContext context) {
    final cfg = ref.watch(configProvider);
    final glassesOn = ref.watch(liveProvider.select((l) => l.glassesConnected));
    final s = _state;
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        backgroundColor: Aurora.base,
        elevation: 0,
        title: const Text('Speak for me',
            style: TextStyle(color: Aurora.textPrimary, fontSize: 17)),
        iconTheme: const IconThemeData(color: Aurora.textPrimary),
        actions: [
          if (s.latest != null)
            IconButton(
              tooltip: 'Show the last line in big text',
              icon: const Icon(Icons.fullscreen_rounded),
              color: Aurora.mint,
              onPressed: () => _BigTextPage.open(context, s.latest!.text,
                  rtl: isRtlLanguage(s.language)),
            ),
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: TextButton(
              onPressed: _pickLanguage,
              child: Text(
                translateLanguageName(s.language),
                style: const TextStyle(color: Aurora.mint, fontSize: 13),
              ),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            _RouteChip(glassesOn: glassesOn),
            if (s.error != null) _ErrorBar(s.error!, onAddVoices: _addVoices),
            Expanded(
              child: s.lines.isEmpty
                  ? const _EmptyState()
                  : _LineList(
                      lines: s.lines,
                      pinned: cfg.speakPhrases,
                      rtl: isRtlLanguage(s.language),
                      onSayAgain: (t) => _speak(t),
                      onTogglePinned: _togglePinned,
                    ),
            ),
            _QuickPhrases(
              phrases: [...kStarterPhrases, ...cfg.speakPhrases],
              onTap: (t) => _speak(t),
            ),
            _Composer(
              input: _input,
              focus: _focus,
              speaking: s.speaking,
              onSpeak: _speak,
              onStop: _controller.stop,
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _addVoices() async {
    final ok = await _voice.openVoiceInstaller();
    if (!ok && mounted) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(const SnackBar(
          content: Text('Could not open the voice settings on this phone.'),
        ));
    }
  }
}

/// Says where the voice will come out, because the whole trick of this
/// screen is that it can be the glasses on someone else's face.
class _RouteChip extends StatelessWidget {
  const _RouteChip({required this.glassesOn});
  final bool glassesOn;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(12, 10, 12, 0),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: Aurora.tint(glassesOn ? Aurora.teal : Aurora.purple, 0.12),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            Icon(
              glassesOn ? Icons.visibility_outlined : Icons.volume_up_outlined,
              size: 16,
              color: glassesOn ? Aurora.mint : Aurora.purpleSoft,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                glassesOn
                    ? 'Your voice plays in the glasses. Hand them to the '
                        'person you are talking to.'
                    : 'Your voice plays on the phone speaker. Connect the '
                        'glasses to speak into someone\'s ear.',
                style: const TextStyle(
                    color: Aurora.textMuted, fontSize: 12, height: 1.4),
              ),
            ),
          ],
        ),
      );
}

class _ErrorBar extends StatelessWidget {
  const _ErrorBar(this.message, {required this.onAddVoices});
  final String message;
  final Future<void> Function() onAddVoices;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(12, 10, 12, 0),
        padding: const EdgeInsets.fromLTRB(12, 9, 4, 4),
        decoration: BoxDecoration(
          color: Aurora.tint(Aurora.danger, 0.14),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(message,
                style: const TextStyle(color: Aurora.danger, fontSize: 12)),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: onAddVoices,
                child: const Text('Add voices',
                    style: TextStyle(color: Aurora.danger, fontSize: 12)),
              ),
            ),
          ],
        ),
      );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) => const Center(
        child: Padding(
          padding: EdgeInsets.symmetric(horizontal: 36),
          child: Text(
            'Type what you want to say and tap Speak. Tap a quick phrase to '
            'say it straight away. Everything you say stays here so you can '
            'say it again with one tap.',
            textAlign: TextAlign.center,
            style:
                TextStyle(color: Aurora.textMuted, fontSize: 13, height: 1.5),
          ),
        ),
      );
}

class _LineList extends StatelessWidget {
  const _LineList({
    required this.lines,
    required this.pinned,
    required this.rtl,
    required this.onSayAgain,
    required this.onTogglePinned,
  });
  final List<SpokenLine> lines;
  final List<String> pinned;
  final bool rtl;
  final ValueChanged<String> onSayAgain;
  final ValueChanged<String> onTogglePinned;

  @override
  Widget build(BuildContext context) => ListView.builder(
        reverse: true,
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
        itemCount: lines.length,
        itemBuilder: (_, i) {
          final line = lines[i];
          final isPinned = pinned.contains(line.text);
          return Semantics(
            button: true,
            label: 'Say again: ${line.text}',
            child: InkWell(
              borderRadius: BorderRadius.circular(12),
              onTap: () => onSayAgain(line.text),
              onLongPress: () => onTogglePinned(line.text),
              child: Container(
                margin: const EdgeInsets.only(bottom: 10),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Aurora.surface,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        line.text,
                        textDirection:
                            rtl ? TextDirection.rtl : TextDirection.ltr,
                        style: TextStyle(
                          color: line.spoken
                              ? Aurora.textPrimary
                              : Aurora.textMuted,
                          fontSize: 18,
                          height: 1.4,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Icon(
                      isPinned
                          ? Icons.push_pin_rounded
                          : (line.spoken
                              ? Icons.replay_rounded
                              : Icons.volume_off_rounded),
                      size: 18,
                      color: isPinned ? Aurora.mint : Aurora.textMuted,
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      );
}

class _QuickPhrases extends StatelessWidget {
  const _QuickPhrases({required this.phrases, required this.onTap});
  final List<String> phrases;
  final ValueChanged<String> onTap;

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 44,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          itemCount: phrases.length,
          separatorBuilder: (_, __) => const SizedBox(width: 8),
          itemBuilder: (_, i) => ActionChip(
            label: Text(phrases[i],
                style:
                    const TextStyle(color: Aurora.textPrimary, fontSize: 13)),
            backgroundColor: Aurora.glass,
            side: const BorderSide(color: Aurora.glassBorder),
            onPressed: () => onTap(phrases[i]),
          ),
        ),
      );
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.input,
    required this.focus,
    required this.speaking,
    required this.onSpeak,
    required this.onStop,
  });
  final TextEditingController input;
  final FocusNode focus;
  final bool speaking;
  final Future<void> Function() onSpeak;
  final Future<void> Function() onStop;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
        decoration: const BoxDecoration(
          border: Border(top: BorderSide(color: Aurora.glassBorder)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: TextField(
                controller: input,
                focusNode: focus,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => onSpeak(),
                style: const TextStyle(
                    color: Aurora.textPrimary, fontSize: 18, height: 1.35),
                decoration: InputDecoration(
                  hintText: 'Type what you want to say',
                  hintStyle: const TextStyle(color: Aurora.textMuted),
                  filled: true,
                  fillColor: Aurora.surface,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
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
            const SizedBox(width: 10),
            Semantics(
              button: true,
              label: speaking ? 'Stop speaking' : 'Speak',
              child: GestureDetector(
                onTap: speaking ? onStop : onSpeak,
                child: Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: speaking ? Aurora.danger : Aurora.teal,
                  ),
                  child: Icon(
                    speaking ? Icons.stop_rounded : Icons.volume_up_rounded,
                    color: speaking ? Colors.white : Aurora.tealInk,
                    size: 26,
                  ),
                ),
              ),
            ),
          ],
        ),
      );
}

/// The last line, as big as the screen allows — for holding the phone up in a
/// place too loud for a phone speaker.
class _BigTextPage extends StatelessWidget {
  const _BigTextPage(this.text, {required this.rtl});
  final String text;
  final bool rtl;

  static Future<void> open(BuildContext context, String text,
          {required bool rtl}) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => _BigTextPage(text, rtl: rtl),
      ));

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Colors.white,
        body: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => Navigator.of(context).pop(),
          child: SafeArea(
            child: Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width - 48,
                    ),
                    child: Text(
                      text,
                      textAlign: TextAlign.center,
                      textDirection:
                          rtl ? TextDirection.rtl : TextDirection.ltr,
                      style: const TextStyle(
                        color: Colors.black,
                        fontSize: 44,
                        fontWeight: FontWeight.w600,
                        height: 1.25,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
}
