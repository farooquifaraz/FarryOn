import 'dart:async';
import 'dart:convert';
import 'dart:ffi' show Abi;

import 'package:http/http.dart' as http;

import 'config.dart';

/// Is there a newer FarryOn to install, or is this one too old to use?
///
/// The app is sideloaded — no Play Store — so nothing updates it behind the
/// user's back (Faraz, 2026-09-26). Two things make it update anyway:
///
///  * at start the app reads the website's `/download/info` (the build the
///    site serves, and the oldest build still served) and offers the new one
///    — or, below the minimum, shows a screen it can't dismiss;
///  * the server refuses a too-old build at hello (`update_required`), which
///    reaches even a copy that never ran this check.
///
/// Builds are compared as BASE builds — the website's "build" (e.g. 2531) —
/// not the phone's versionCode, which Flutter's split-per-abi offsets by
/// ABI (+1000 arm32, +2000 arm64, +4000 x86_64). The server does the same
/// sums (backend app/core/app_version.py).
class AppUpdate {
  AppUpdate._();

  static const Map<String, int> _offsets = {
    'arm32': 1000,
    'arm64': 2000,
    'x86_64': 4000,
  };

  /// This phone's APK flavour, as the website names its downloads.
  static String abi() {
    try {
      final a = Abi.current();
      if (a == Abi.androidArm64) return 'arm64';
      if (a == Abi.androidArm) return 'arm32';
      if (a == Abi.androidX64) return 'x86_64';
    } catch (_) {}
    return '';
  }

  /// The base build of `"1.0.0+4531"` for [abi] (→ 2531); null without a
  /// build number.
  static int? baseBuild(String installedVersion, String abi) {
    final plus = installedVersion.lastIndexOf('+');
    if (plus < 0) return null;
    final code = int.tryParse(installedVersion.substring(plus + 1).trim());
    if (code == null || code <= 0) return null;
    final offset = _offsets[abi] ??
        switch (code ~/ 1000) { 3 => 1000, 4 => 2000, 6 => 4000, _ => 0 };
    return code - offset;
  }

  /// Ask the server this app talks to. Null when it can't tell (no network,
  /// no build published there, a build number it can't read) — an update
  /// prompt is never shown on a guess.
  static Future<UpdateStatus?> check(
    AppConfig config, {
    http.Client? client,
    String? installedVersion,
    String? abiOverride,
    Duration timeout = const Duration(seconds: 6),
  }) async {
    final abi = abiOverride ?? AppUpdate.abi();
    final current =
        baseBuild(installedVersion ?? AppConfig.installedVersion, abi);
    if (current == null) return null;
    final c = client ?? http.Client();
    try {
      final res = await c
          .get(config.httpBase.replace(path: '/download/info'))
          .timeout(timeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map) return null;
      final build = body['build'];
      final latest = build is Map ? (build['build'] as num?)?.toInt() : null;
      final min = (body['minBuild'] as num?)?.toInt() ?? 0;
      final flavour = abi == 'arm32' ? 'arm32' : 'arm64';
      final avail = body[flavour];
      final downloadable = avail is Map && avail['available'] == true;
      return UpdateStatus(
        current: current,
        latest: latest,
        minimum: min,
        downloadable: downloadable,
        downloadUrl: config.httpBase.replace(path: '/download/$flavour'),
      );
    } catch (_) {
      return null;
    } finally {
      if (client == null) c.close();
    }
  }
}

/// What [AppUpdate.check] found.
class UpdateStatus {
  const UpdateStatus({
    required this.current,
    required this.latest,
    required this.minimum,
    required this.downloadable,
    required this.downloadUrl,
  });

  final int current;
  final int? latest;
  final int minimum;
  final bool downloadable;
  final Uri downloadUrl;

  /// Below the oldest build still served: the app can't be used.
  bool get required => minimum > 0 && current < minimum;

  /// A newer build is on the website, ready to download.
  bool get available =>
      downloadable && latest != null && latest! > current;
}
