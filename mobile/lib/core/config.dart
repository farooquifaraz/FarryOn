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
    this.emailAddress,
    this.emailAppPassword,
    this.emailProvider = 'gmail',
    this.emailImapHost,
    this.emailSmtpHost,
    this.emailSmtpPort = 587,
    this.handsFree = true,
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

  /// Email (IMAP) so the assistant can read the user's recent mail. The
  /// address + an app-specific password (e.g. a Gmail App Password). Sent
  /// per-session in `hello.email`; the backend never persists it.
  final String? emailAddress;
  final String? emailAppPassword;

  /// Mail provider preset (`gmail` | `outlook` | `yahoo` | `hostinger` |
  /// `custom`) plus the resolved IMAP/SMTP hosts so any provider works — the
  /// user reads and sends from their OWN mailbox.
  final String emailProvider;
  final String? emailImapHost;
  final String? emailSmtpHost;
  final int emailSmtpPort;

  /// Hands-free (default): the mic opens automatically and the provider's VAD
  /// handles turn-taking. When false, it's TAP-TO-TALK — the mic stays closed
  /// until the user taps it, so background noise / a TV / the assistant's own
  /// voice can never trigger a phantom turn. Best in noisy rooms.
  final bool handsFree;

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
    String? emailAddress,
    String? emailAppPassword,
    String? emailProvider,
    String? emailImapHost,
    String? emailSmtpHost,
    int? emailSmtpPort,
    bool? handsFree,
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
        emailAddress: emailAddress ?? this.emailAddress,
        emailAppPassword: emailAppPassword ?? this.emailAppPassword,
        emailProvider: emailProvider ?? this.emailProvider,
        emailImapHost: emailImapHost ?? this.emailImapHost,
        emailSmtpHost: emailSmtpHost ?? this.emailSmtpHost,
        emailSmtpPort: emailSmtpPort ?? this.emailSmtpPort,
        handsFree: handsFree ?? this.handsFree,
      );

  @override
  String toString() => 'AppConfig(${secure ? "wss" : "ws"}://$host:$port)';
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
