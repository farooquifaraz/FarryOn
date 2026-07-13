import 'protocol_url.dart';

/// App configuration: where the backend lives and a few runtime toggles.
///
/// The backend host/port is intentionally *not* hard-coded into the client.
/// Resolution order (first match wins):
///   1. A `--dart-define` value (compile/run time), e.g.
///      `flutter run --dart-define=FARRYON_HOST=192.168.1.50 \
///                   --dart-define=FARRYON_PORT=8000 \
///                   --dart-define=FARRYON_SECURE=false`
///   2. The defaults below (localhost:8000, plaintext `ws://`).
///
/// At runtime the user can override the host/port/scheme from the settings
/// sheet; [AppConfig.copyWith] produces the updated immutable config which the
/// controller re-connects with.
class AppConfig {
  const AppConfig({
    required this.host,
    required this.port,
    required this.secure,
    this.authToken,
    this.appVersion = '1.0.0',
    this.provider = 'gemini',
    this.webSearchProvider = 'tavily',
    this.webSearchApiKey,
    this.webSearchFallbackProvider = 'serper',
    this.webSearchFallbackApiKey,
    this.emailAccounts = const [],
    this.handsFree = true,
    this.saveCapturesToGallery = true,
    this.glassesRetentionDays = 0,
  });

  /// Backend host (IP or DNS name), without scheme or port.
  final String host;

  /// Backend TCP port.
  final int port;

  /// When true use `wss://` (TLS); otherwise `ws://`.
  final bool secure;

  /// Optional short-lived auth token, sent as `?token=` (see `PROTOCOL.md` §1).
  final String? authToken;

  /// Reported in the `hello` message.
  final String appVersion;

  /// AI provider the backend should use for this session: `gemini` | `openai`
  /// | `grok` | `mock`. Sent in `hello.provider`; changing it reconnects.
  final String provider;

  /// Web search: primary provider (`tavily` | `serper` | `serpapi` | `mock`)
  /// and its key, plus an optional fallback used when the primary runs out of
  /// free credits. Sent per-session in `hello.webSearch`.
  final String webSearchProvider;
  final String? webSearchApiKey;
  final String webSearchFallbackProvider;
  final String? webSearchFallbackApiKey;

  /// Configured mail accounts (0, 1, or 2) so the assistant can read and send
  /// from the user's OWN mailboxes. Farry uses [primaryEmailAccount] unless the
  /// user names another by its [EmailAccount.label]. App passwords live in the
  /// device keystore — they are NOT part of the persisted list, only carried in
  /// memory on the loaded config and sent per-session in `hello`.
  final List<EmailAccount> emailAccounts;

  /// The account Farry reads/sends from unless told otherwise: the one flagged
  /// primary, else the first configured, else null (no mailbox set up).
  EmailAccount? get primaryEmailAccount {
    if (emailAccounts.isEmpty) return null;
    return emailAccounts.firstWhere(
      (a) => a.primary,
      orElse: () => emailAccounts.first,
    );
  }

  /// The accounts that are actually usable (address + app password present).
  List<EmailAccount> get usableEmailAccounts =>
      emailAccounts.where((a) => a.isComplete).toList();

  /// Hands-free (default): the mic opens automatically and the provider's VAD
  /// handles turn-taking. When false, it's TAP-TO-TALK — the mic stays closed
  /// until the user taps it, so background noise / a TV / the assistant's own
  /// voice can never trigger a phantom turn. Best in noisy rooms.
  final bool handsFree;

  /// Save every live capture (phone camera / glasses still) into the phone
  /// gallery (`Pictures/Farry`). Default on.
  final bool saveCapturesToGallery;

  /// Auto-delete photos from the SMART-GLASSES storage after they've synced to
  /// the phone, to stop the glasses filling up. Value is a day count:
  /// `0` = never delete, `-2` = delete right after each photo syncs to the
  /// phone, `1` / `7` / `30` = delete synced photos older than that many days,
  /// `-1` = only when the glasses report storage full.
  final int glassesRetentionDays;

  /// Build the initial config from `--dart-define` values, falling back to
  /// localhost defaults suitable for an emulator talking to a host backend.
  ///
  /// Note: on the Android emulator, `10.0.2.2` maps to the host machine's
  /// `localhost`; that makes a good default override during development.
  factory AppConfig.fromEnvironment() {
    const host = String.fromEnvironment(
      'FARRYON_HOST',
      defaultValue: 'localhost',
    );
    const port = int.fromEnvironment('FARRYON_PORT', defaultValue: 8000);
    const secure = bool.fromEnvironment('FARRYON_SECURE', defaultValue: false);
    const token = String.fromEnvironment('FARRYON_TOKEN', defaultValue: '');
    const provider =
        String.fromEnvironment('FARRYON_PROVIDER', defaultValue: 'gemini');
    return AppConfig(
      host: host,
      port: port,
      secure: secure,
      authToken: token.isEmpty ? null : token,
      provider: provider,
    );
  }

  /// The fully-resolved `/ws/live` endpoint, including scheme, port, and the
  /// optional `?token=` query parameter.
  Uri get liveUri => buildLiveUri(
        host: host,
        port: port,
        secure: secure,
        token: authToken,
      );

  /// Base `http(s)://host:port` for REST calls (Notes/Tasks).
  Uri get httpBase => Uri(
        scheme: secure ? 'https' : 'http',
        host: host,
        port: port,
      );

  AppConfig copyWith({
    String? host,
    int? port,
    bool? secure,
    String? authToken,
    bool clearToken = false,
    String? appVersion,
    String? provider,
    String? webSearchProvider,
    String? webSearchApiKey,
    String? webSearchFallbackProvider,
    String? webSearchFallbackApiKey,
    List<EmailAccount>? emailAccounts,
    bool? handsFree,
    bool? saveCapturesToGallery,
    int? glassesRetentionDays,
  }) =>
      AppConfig(
        host: host ?? this.host,
        port: port ?? this.port,
        secure: secure ?? this.secure,
        authToken: clearToken ? null : (authToken ?? this.authToken),
        appVersion: appVersion ?? this.appVersion,
        provider: provider ?? this.provider,
        webSearchProvider: webSearchProvider ?? this.webSearchProvider,
        webSearchApiKey: webSearchApiKey ?? this.webSearchApiKey,
        webSearchFallbackProvider:
            webSearchFallbackProvider ?? this.webSearchFallbackProvider,
        webSearchFallbackApiKey:
            webSearchFallbackApiKey ?? this.webSearchFallbackApiKey,
        emailAccounts: emailAccounts ?? this.emailAccounts,
        handsFree: handsFree ?? this.handsFree,
        saveCapturesToGallery:
            saveCapturesToGallery ?? this.saveCapturesToGallery,
        glassesRetentionDays:
            glassesRetentionDays ?? this.glassesRetentionDays,
      );

  @override
  String toString() => 'AppConfig(${secure ? "wss" : "ws"}://$host:$port)';
}

/// One configured mailbox. The [label] ("Personal", "Work") is how the user
/// and Farry refer to it; [primary] marks the default account.
///
/// [appPassword] is deliberately NOT part of [toMap]/[fromMap] — it is stored
/// in the device keystore and injected onto the loaded account in memory. So a
/// persisted account JSON never contains a secret.
class EmailAccount {
  const EmailAccount({
    required this.id,
    required this.label,
    required this.address,
    this.appPassword,
    this.provider = 'gmail',
    this.imapHost,
    this.smtpHost,
    this.smtpPort = 587,
    this.primary = false,
  });

  /// Stable per-account id, also the keystore key suffix. Never reused.
  final String id;
  final String label;
  final String address;

  /// App-specific password / mailbox password. In memory only; keystore-backed.
  final String? appPassword;

  /// `gmail` | `outlook` | `yahoo` | `hostinger` | `custom`.
  final String provider;
  final String? imapHost;
  final String? smtpHost;
  final int smtpPort;
  final bool primary;

  /// Usable = we have both an address and a password to log in with.
  bool get isComplete =>
      address.trim().isNotEmpty && (appPassword?.trim().isNotEmpty ?? false);

  /// Resolve the IMAP/SMTP hosts, filling from the provider preset unless this
  /// is a `custom` account (which carries its own typed hosts).
  String get resolvedImapHost {
    if (provider == 'custom') return imapHost ?? '';
    return EmailProviders.presets[provider]?.imap ?? imapHost ?? '';
  }

  String get resolvedSmtpHost {
    if (provider == 'custom') return smtpHost ?? '';
    return EmailProviders.presets[provider]?.smtp ?? smtpHost ?? '';
  }

  int get resolvedSmtpPort {
    if (provider == 'custom') return smtpPort;
    return EmailProviders.presets[provider]?.port ?? smtpPort;
  }

  EmailAccount copyWith({
    String? id,
    String? label,
    String? address,
    String? appPassword,
    String? provider,
    String? imapHost,
    String? smtpHost,
    int? smtpPort,
    bool? primary,
  }) =>
      EmailAccount(
        id: id ?? this.id,
        label: label ?? this.label,
        address: address ?? this.address,
        appPassword: appPassword ?? this.appPassword,
        provider: provider ?? this.provider,
        imapHost: imapHost ?? this.imapHost,
        smtpHost: smtpHost ?? this.smtpHost,
        smtpPort: smtpPort ?? this.smtpPort,
        primary: primary ?? this.primary,
      );

  /// Non-secret fields only — safe to persist in plain `shared_preferences`.
  Map<String, Object?> toMap() => {
        'id': id,
        'label': label,
        'address': address,
        'provider': provider,
        'imapHost': imapHost,
        'smtpHost': smtpHost,
        'smtpPort': smtpPort,
        'primary': primary,
      };

  factory EmailAccount.fromMap(Map<String, Object?> m) => EmailAccount(
        id: (m['id'] as String?) ?? newId(),
        label: (m['label'] as String?) ?? 'Email',
        address: (m['address'] as String?) ?? '',
        provider: (m['provider'] as String?) ?? 'gmail',
        imapHost: m['imapHost'] as String?,
        smtpHost: m['smtpHost'] as String?,
        smtpPort: (m['smtpPort'] as num?)?.toInt() ?? 587,
        primary: (m['primary'] as bool?) ?? false,
      );

  /// A fresh, collision-resistant id for a newly-added account.
  static String newId() =>
      'a${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}';
}

/// Known mail-provider presets so users connect their OWN mailbox in one tap.
/// `custom` leaves the hosts blank for the user to fill.
class EmailProviders {
  EmailProviders._();

  static const Map<String,
          ({String label, String imap, String smtp, int port})>
      presets = {
    'gmail': (
      label: 'Gmail',
      imap: 'imap.gmail.com',
      smtp: 'smtp.gmail.com',
      port: 587,
    ),
    'outlook': (
      label: 'Outlook / 365',
      imap: 'outlook.office365.com',
      smtp: 'smtp.office365.com',
      port: 587,
    ),
    'yahoo': (
      label: 'Yahoo',
      imap: 'imap.mail.yahoo.com',
      smtp: 'smtp.mail.yahoo.com',
      port: 465,
    ),
    'hostinger': (
      label: 'Hostinger',
      imap: 'imap.hostinger.com',
      smtp: 'smtp.hostinger.com',
      port: 465,
    ),
    'custom': (label: 'Custom', imap: '', smtp: '', port: 587),
  };
}
