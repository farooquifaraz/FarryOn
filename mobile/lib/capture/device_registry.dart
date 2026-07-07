import '../core/logger.dart';
import 'capture_source.dart';
import 'glasses_capture_source.dart';
import 'phone_capture_source.dart';

/// The kinds of capture devices the app can switch between.
enum CaptureDeviceKind { phone, glasses }

/// Owns the set of available [CaptureSource]s and tracks the active ones.
///
/// This is the switchboard for the universal device-adapter layer: the UI and
/// controller ask the registry for the *active* source and never construct
/// concrete sources themselves.
///
/// **B1-B: audio and vision are chosen independently.** The mic can come from
/// one device (e.g. glasses PCM, or the phone/earbuds route) while the camera
/// comes from another (e.g. the phone). [audioSource] and [videoSource] may be
/// the same instance (both phone) or two different ones. Sources are created
/// lazily and cached so a source that backs both channels is initialized once.
class DeviceRegistry {
  DeviceRegistry({
    CaptureDeviceKind audio = CaptureDeviceKind.phone,
    CaptureDeviceKind video = CaptureDeviceKind.phone,
    CaptureSource Function(CaptureDeviceKind kind)? factory,
  })  : _audioKind = audio,
        _videoKind = video,
        _factory = factory ?? _defaultFactory;

  static final _log = Logger('DeviceRegistry');

  final CaptureSource Function(CaptureDeviceKind kind) _factory;
  final Map<CaptureDeviceKind, CaptureSource> _sources = {};

  CaptureDeviceKind _audioKind;
  CaptureDeviceKind _videoKind;

  /// Device supplying the microphone.
  CaptureDeviceKind get audioKind => _audioKind;

  /// Device supplying the camera.
  CaptureDeviceKind get videoKind => _videoKind;

  /// All device kinds the registry knows how to create.
  List<CaptureDeviceKind> get availableKinds => CaptureDeviceKind.values;

  /// The source backing the microphone (created on first access).
  CaptureSource get audioSource => _sourceFor(_audioKind);

  /// The source backing the camera (created on first access).
  CaptureSource get videoSource => _sourceFor(_videoKind);

  CaptureSource _sourceFor(CaptureDeviceKind kind) =>
      _sources.putIfAbsent(kind, () {
        _log.info('creating capture source for $kind');
        return _factory(kind);
      });

  /// Select the microphone device; returns the newly-active audio source.
  CaptureSource setAudioKind(CaptureDeviceKind kind) {
    _audioKind = kind;
    _log.info('audio device → $kind');
    return audioSource;
  }

  /// Select the camera device; returns the newly-active video source.
  CaptureSource setVideoKind(CaptureDeviceKind kind) {
    _videoKind = kind;
    _log.info('video device → $kind');
    return videoSource;
  }

  /// Dispose every source the registry instantiated.
  Future<void> dispose() async {
    for (final source in _sources.values) {
      await source.dispose();
    }
    _sources.clear();
  }

  static CaptureSource _defaultFactory(CaptureDeviceKind kind) =>
      switch (kind) {
        CaptureDeviceKind.phone => PhoneCaptureSource(),
        CaptureDeviceKind.glasses => GlassesCaptureSource(),
      };
}
