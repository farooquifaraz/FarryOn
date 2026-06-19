import '../core/logger.dart';
import 'capture_source.dart';
import 'glasses_capture_source.dart';
import 'phone_capture_source.dart';

/// The kinds of capture devices the app can switch between.
enum CaptureDeviceKind { phone, glasses }

/// Owns the set of available [CaptureSource]s and tracks the active one.
///
/// This is the switchboard for the universal device-adapter layer: the UI and
/// controller ask the registry for the *active* source and never construct
/// concrete sources themselves. Adding a new device type is a matter of
/// teaching [create] (or registering an instance) about it.
///
/// Sources are created lazily and cached so switching back and forth doesn't
/// re-acquire hardware unnecessarily. The registry disposes everything it
/// created in [dispose].
class DeviceRegistry {
  DeviceRegistry({
    CaptureDeviceKind initial = CaptureDeviceKind.phone,
    CaptureSource Function(CaptureDeviceKind kind)? factory,
  })  : _activeKind = initial,
        _factory = factory ?? _defaultFactory;

  static final _log = Logger('DeviceRegistry');

  final CaptureSource Function(CaptureDeviceKind kind) _factory;
  final Map<CaptureDeviceKind, CaptureSource> _sources = {};

  CaptureDeviceKind _activeKind;

  /// The kind of the currently-selected device.
  CaptureDeviceKind get activeKind => _activeKind;

  /// All device kinds the registry knows how to create.
  List<CaptureDeviceKind> get availableKinds => CaptureDeviceKind.values;

  /// The currently-active capture source (created on first access).
  CaptureSource get active => _sourceFor(_activeKind);

  CaptureSource _sourceFor(CaptureDeviceKind kind) =>
      _sources.putIfAbsent(kind, () {
        _log.info('creating capture source for $kind');
        return _factory(kind);
      });

  /// Switch the active device. Returns the newly-active source. The previously
  /// active source is left initialized but its streams should be stopped by the
  /// controller before switching.
  CaptureSource switchTo(CaptureDeviceKind kind) {
    _activeKind = kind;
    _log.info('active device → $kind');
    return _sourceFor(kind);
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
