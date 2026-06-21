import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';

/// Persists [AppConfig] across launches via `shared_preferences`, so the user
/// configures the backend host, provider, and keys once. Values fall back to
/// the `--dart-define` / localhost defaults when nothing is saved yet.
class ConfigStore {
  ConfigStore._();

  static SharedPreferences? _prefs;

  /// Must be awaited once at startup (in `main`) before [load].
  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  static AppConfig load() {
    final p = _prefs;
    final base = AppConfig.fromEnvironment();
    if (p == null) return base;
    return base.copyWith(
      host: p.getString('cfg.host'),
      port: p.getInt('cfg.port'),
      secure: p.getBool('cfg.secure'),
      provider: p.getString('cfg.provider'),
      webSearchProvider: p.getString('cfg.ws.provider'),
      webSearchApiKey: p.getString('cfg.ws.key'),
      webSearchFallbackProvider: p.getString('cfg.ws.fbProvider'),
      webSearchFallbackApiKey: p.getString('cfg.ws.fbKey'),
    );
  }

  static Future<void> save(AppConfig c) async {
    final p = _prefs;
    if (p == null) return;
    await p.setString('cfg.host', c.host);
    await p.setInt('cfg.port', c.port);
    await p.setBool('cfg.secure', c.secure);
    await p.setString('cfg.provider', c.provider);
    await p.setString('cfg.ws.provider', c.webSearchProvider);
    await p.setString('cfg.ws.key', c.webSearchApiKey ?? '');
    await p.setString('cfg.ws.fbProvider', c.webSearchFallbackProvider);
    await p.setString('cfg.ws.fbKey', c.webSearchFallbackApiKey ?? '');
  }
}
