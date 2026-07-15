import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';

/// Persists [AppConfig] across launches. Most settings live in
/// `shared_preferences`; email **app passwords** live in the platform keystore
/// (`flutter_secure_storage`) and are never written to plain prefs. The
/// non-secret account fields (label, address, provider, hosts, primary flag)
/// are stored as a JSON array under [_accountsKey].
class ConfigStore {
  ConfigStore._();

  static SharedPreferences? _prefs;

  static const FlutterSecureStorage _secure = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static const String _accountsKey = 'cfg.email.accounts';
  static String _pwKey(String id) => 'email.pw.$id';

  // Auth session (FarryOn account) — tokens in the keystore, identity in prefs.
  static const String _authAccessKey = 'auth.access';
  static const String _authRefreshKey = 'auth.refresh';
  static const String _authEmailKey = 'auth.email';
  static const String _authNameKey = 'auth.name';
  static const String _authUserIdKey = 'auth.userId';

  /// account id -> app password, hydrated from the keystore during [init] so
  /// the synchronous [load] can attach secrets without an await.
  static final Map<String, String> _pwCache = {};

  /// Auth tokens, hydrated from the keystore during [init] (same pattern as
  /// [_pwCache]) so the synchronous [authSession] works without an await.
  static String? _accessCache;
  static String? _refreshCache;

  /// Must be awaited once at startup (in `main`) before [load].
  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    await _migrateLegacyEmail();
    await _hydrateSecrets();
    _accessCache = await _secure.read(key: _authAccessKey);
    _refreshCache = await _secure.read(key: _authRefreshKey);
  }

  // ---- Auth session --------------------------------------------------------

  /// The persisted sign-in, or null when signed out. Synchronous — tokens are
  /// hydrated in [init].
  static ({
    String access,
    String refresh,
    String email,
    String? displayName,
    int? userId,
  })? authSession() {
    final access = _accessCache;
    final refresh = _refreshCache;
    if (access == null || access.isEmpty || refresh == null || refresh.isEmpty) {
      return null;
    }
    return (
      access: access,
      refresh: refresh,
      email: _prefs?.getString(_authEmailKey) ?? '',
      displayName: _prefs?.getString(_authNameKey),
      userId: _prefs?.getInt(_authUserIdKey),
    );
  }

  static Future<void> saveAuthSession({
    required String access,
    required String refresh,
    String? email,
    String? displayName,
    int? userId,
  }) async {
    await _secure.write(key: _authAccessKey, value: access);
    await _secure.write(key: _authRefreshKey, value: refresh);
    _accessCache = access;
    _refreshCache = refresh;
    final p = _prefs;
    if (p != null) {
      if (email != null) await p.setString(_authEmailKey, email);
      if (displayName != null) await p.setString(_authNameKey, displayName);
      if (userId != null) await p.setInt(_authUserIdKey, userId);
    }
  }

  static Future<void> clearAuthSession() async {
    await _secure.delete(key: _authAccessKey);
    await _secure.delete(key: _authRefreshKey);
    _accessCache = null;
    _refreshCache = null;
    final p = _prefs;
    if (p != null) {
      await p.remove(_authEmailKey);
      await p.remove(_authNameKey);
      await p.remove(_authUserIdKey);
    }
  }

  static AppConfig load() {
    final p = _prefs;
    final base = AppConfig.fromEnvironment();
    if (p == null) return base;
    // Grok is retired from the UI (too slow); move anyone saved on it to Gemini.
    var provider = p.getString('cfg.provider');
    if (provider == 'grok') provider = 'gemini';
    return base.copyWith(
      host: p.getString('cfg.host'),
      port: p.getInt('cfg.port'),
      secure: p.getBool('cfg.secure'),
      provider: provider,
      webSearchProvider: p.getString('cfg.ws.provider'),
      webSearchApiKey: p.getString('cfg.ws.key'),
      webSearchFallbackProvider: p.getString('cfg.ws.fbProvider'),
      webSearchFallbackApiKey: p.getString('cfg.ws.fbKey'),
      emailAccounts: _loadAccounts(),
      handsFree: p.getBool('cfg.handsFree'),
      saveCapturesToGallery: p.getBool('cfg.saveCapturesToGallery'),
      glassesRetentionDays: p.getInt('cfg.glassesRetentionDays'),
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
    await _saveAccounts(c.emailAccounts);
    await p.setBool('cfg.handsFree', c.handsFree);
    await p.setBool('cfg.saveCapturesToGallery', c.saveCapturesToGallery);
    await p.setInt('cfg.glassesRetentionDays', c.glassesRetentionDays);
  }

  // ---- Email accounts ----------------------------------------------------

  static List<EmailAccount> _loadAccounts() {
    final raw = _prefs?.getString(_accountsKey);
    if (raw == null || raw.isEmpty) return const [];
    try {
      final list = jsonDecode(raw) as List;
      return [
        for (final m in list)
          _withSecret(EmailAccount.fromMap(
              (m as Map).cast<String, Object?>())),
      ];
    } catch (_) {
      return const [];
    }
  }

  static EmailAccount _withSecret(EmailAccount a) {
    final pw = _pwCache[a.id];
    return (pw != null && pw.isNotEmpty) ? a.copyWith(appPassword: pw) : a;
  }

  static Future<void> _saveAccounts(List<EmailAccount> accounts) async {
    final p = _prefs;
    if (p == null) return;
    // Non-secret fields → prefs.
    await p.setString(
      _accountsKey,
      jsonEncode([for (final a in accounts) a.toMap()]),
    );
    // Secrets → keystore; keep the in-memory cache in step.
    final keep = <String>{};
    for (final a in accounts) {
      keep.add(a.id);
      final pw = a.appPassword?.trim() ?? '';
      if (pw.isNotEmpty) {
        await _secure.write(key: _pwKey(a.id), value: pw);
        _pwCache[a.id] = pw;
      } else {
        await _secure.delete(key: _pwKey(a.id));
        _pwCache.remove(a.id);
      }
    }
    // Purge secrets for accounts that were removed.
    for (final id in _pwCache.keys.toList()) {
      if (!keep.contains(id)) {
        await _secure.delete(key: _pwKey(id));
        _pwCache.remove(id);
      }
    }
  }

  static Future<void> _hydrateSecrets() async {
    _pwCache.clear();
    for (final id in _accountIds()) {
      final pw = await _secure.read(key: _pwKey(id));
      if (pw != null && pw.isNotEmpty) _pwCache[id] = pw;
    }
  }

  static List<String> _accountIds() {
    final raw = _prefs?.getString(_accountsKey);
    if (raw == null || raw.isEmpty) return const [];
    try {
      final list = jsonDecode(raw) as List;
      return [
        for (final m in list) ((m as Map)['id'] as String?) ?? '',
      ].where((s) => s.isNotEmpty).toList();
    } catch (_) {
      return const [];
    }
  }

  /// One-time upgrade: fold the pre-multi-account single mailbox
  /// (`cfg.email.*` flat keys, plaintext password) into an [EmailAccount] and
  /// move its password into the keystore — then delete the plaintext copy.
  static Future<void> _migrateLegacyEmail() async {
    final p = _prefs;
    if (p == null) return;
    if (p.containsKey(_accountsKey)) return; // already on the new format
    final addr = (p.getString('cfg.email.addr') ?? '').trim();
    if (addr.isEmpty) return; // nothing configured before
    final acct = EmailAccount(
      id: 'primary',
      label: 'Personal',
      address: addr,
      provider: p.getString('cfg.email.provider') ?? 'gmail',
      imapHost: _blankToNull(p.getString('cfg.email.imap')),
      smtpHost: _blankToNull(p.getString('cfg.email.smtp')),
      smtpPort: p.getInt('cfg.email.smtpPort') ?? 587,
      primary: true,
    );
    await p.setString(_accountsKey, jsonEncode([acct.toMap()]));
    final pw = p.getString('cfg.email.pw') ?? '';
    if (pw.isNotEmpty) await _secure.write(key: _pwKey('primary'), value: pw);
    // Remove the plaintext password (and the now-migrated flat keys).
    await p.remove('cfg.email.pw');
    await p.remove('cfg.email.addr');
    await p.remove('cfg.email.provider');
    await p.remove('cfg.email.imap');
    await p.remove('cfg.email.smtp');
    await p.remove('cfg.email.smtpPort');
  }

  static String? _blankToNull(String? s) =>
      (s == null || s.trim().isEmpty) ? null : s;
}
