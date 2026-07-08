import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../capture/capture_source.dart';
import '../capture/device_registry.dart';
import '../core/config.dart';
import '../core/config_store.dart';
import '../data/data_api.dart';
import '../data/finder_api.dart';
import '../data/live_client.dart';
import '../features/glasses_lab/bridge/glasses_channel.dart';
import '../playback/pcm_player.dart';
import '../protocol/messages.dart';
import 'live_controller.dart';
import 'live_state.dart';
import 'permissions.dart';

/// App configuration. Seeded from persisted storage (falling back to
/// `--dart-define`s / localhost); overridable at runtime via the settings
/// sheet. Changes are saved by the [LiveNotifier] so they survive restarts.
final configProvider = StateProvider<AppConfig>((ref) => ConfigStore.load());

/// The capture-device registry (phone ⇄ glasses switchboard).
final deviceRegistryProvider = Provider<DeviceRegistry>((ref) {
  final registry = DeviceRegistry();
  ref.onDispose(registry.dispose);
  return registry;
});

/// The TTS playback engine (PCM16 24 kHz).
final pcmPlayerProvider = Provider<PcmPlayer>((ref) {
  final player = PcmPlayer();
  ref.onDispose(player.dispose);
  return player;
});

/// Mic/camera permission service.
final permissionsProvider = Provider<PermissionsService>(
  (ref) => PermissionsService(),
);

/// REST client for the Notes/Tasks view; tracks the current backend config.
final dataApiProvider = Provider<DataApi>((ref) {
  final api = DataApi(ref.read(configProvider));
  ref.listen<AppConfig>(configProvider, (_, next) => api.updateConfig(next));
  ref.onDispose(api.dispose);
  return api;
});

/// REST client for the landmark/product Finder (`POST /detect`); tracks config.
final finderApiProvider = Provider<FinderApi>((ref) {
  final api = FinderApi(ref.read(configProvider));
  ref.listen<AppConfig>(configProvider, (_, next) => api.updateConfig(next));
  ref.onDispose(api.dispose);
  return api;
});

/// The orchestrating [LiveController].
///
/// Reads the current [configProvider]; when config changes the controller is
/// told to re-point and reconnect via [LiveController.updateConfig] (wired in
/// the [LiveNotifier]).
final liveControllerProvider = Provider<LiveController>((ref) {
  final registry = ref.watch(deviceRegistryProvider);
  final player = ref.watch(pcmPlayerProvider);
  final permissions = ref.watch(permissionsProvider);
  final config = ref.read(configProvider);

  WebSocketLiveClient clientFactory(
    AppConfig cfg,
    DeviceInfo Function() deviceInfo,
  ) =>
      WebSocketLiveClient(
        config: cfg,
        platform: LiveController.defaultPlatform,
        deviceInfoProvider: deviceInfo,
      );

  final controller = LiveController(
    config: config,
    registry: registry,
    player: player,
    permissions: permissions,
    clientFactory: clientFactory,
    glassesBridge: GlassesChannel.shared,
  );
  ref.onDispose(controller.dispose);
  return controller;
});

/// Riverpod [Notifier] exposing [LiveSessionState] to the UI and forwarding
/// user intents to the [LiveController].
class LiveNotifier extends Notifier<LiveSessionState> {
  late LiveController _controller;

  @override
  LiveSessionState build() {
    _controller = ref.watch(liveControllerProvider);

    final sub = _controller.stateStream.listen((s) => state = s);
    ref.onDispose(sub.cancel);

    // React to config changes (settings sheet): persist them and re-point the
    // client so the new host/provider/keys take effect on reconnect.
    ref.listen<AppConfig>(configProvider, (prev, next) {
      if (prev != next) {
        ConfigStore.save(next);
        _controller.updateConfig(next);
      }
    });

    return _controller.state;
  }

  // ---- Intents (thin pass-throughs) -------------------------------------

  Future<PermissionOutcome> connect() => _controller.connect();
  Future<void> disconnect() => _controller.disconnect();
  Future<void> toggleMic() => _controller.toggleMic();
  Future<void> startListening() => _controller.startListening();
  Future<void> stopListening() => _controller.stopListening();
  Future<void> interrupt() => _controller.interrupt();
  void sendText(String text) => _controller.sendText(text);
  Future<void> setCameraEnabled(bool on) => _controller.setCameraEnabled(on);
  Future<void> setCameraPortrait(bool portrait) =>
      _controller.setCameraPortrait(portrait);
  Future<void> setCameraZoom(double level) =>
      _controller.setCameraZoom(level);
  Future<void> setAudioDevice(CaptureDeviceKind kind) =>
      _controller.setAudioDevice(kind);
  Future<void> setVideoDevice(CaptureDeviceKind kind) =>
      _controller.setVideoDevice(kind);
  void respondToolPermission(String id, bool granted) =>
      _controller.respondToolPermission(id, granted);
  void dismissError() => _controller.dismissError();

  /// The current camera source (for the camera preview widget).
  CaptureSource get activeSource =>
      ref.read(deviceRegistryProvider).videoSource;
}

/// Primary UI entry point: the live session state + intents.
final liveProvider =
    NotifierProvider<LiveNotifier, LiveSessionState>(LiveNotifier.new);
