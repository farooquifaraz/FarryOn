import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../playback/pcm_player.dart';
import '../../state/providers.dart';
import '../glasses_lab/bridge/glasses_channel.dart';
import 'translate_controller.dart';
import 'translate_state.dart';

/// The translate session's **own** PCM player.
///
/// Not `pcmPlayerProvider`: that one's drain clock is what the assistant's echo
/// guard reads to decide when to mute the microphone. Feeding translated audio
/// into it would deafen the assistant's mic for the length of every
/// translation — a bug that already cost us a day once, from the other
/// direction (see the mic-gate note in `live_controller.dart`).
final translatePlayerProvider = Provider<PcmPlayer>((ref) {
  final player = PcmPlayer();
  ref.onDispose(player.dispose);
  return player;
});

/// The [TranslateController]. Shares the device registry with the live session
/// — only one of them owns the microphone at a time, because the translate
/// screen disconnects the assistant on entry.
final translateControllerProvider = Provider<TranslateController>((ref) {
  final controller = TranslateController(
    config: ref.read(configProvider),
    registry: ref.watch(deviceRegistryProvider),
    player: ref.watch(translatePlayerProvider),
    permissions: ref.watch(permissionsProvider),
    // Only so the session can notice the glasses dropping while they ARE the
    // microphone, and fall back to the phone. It never drives them.
    glasses: GlassesChannel.shared,
  );
  ref.onDispose(controller.dispose);
  return controller;
});

/// The translate screen's state.
final translateProvider =
    NotifierProvider<TranslateNotifier, TranslateState>(TranslateNotifier.new);

class TranslateNotifier extends Notifier<TranslateState> {
  @override
  TranslateState build() {
    final controller = ref.watch(translateControllerProvider);
    final sub = controller.stateStream.listen((s) => state = s);
    ref.onDispose(sub.cancel);
    return controller.state;
  }
}
