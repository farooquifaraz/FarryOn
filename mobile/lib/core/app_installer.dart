import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

/// Fetches an update APK and hands it to Android's package installer.
///
/// Sending the download to the browser stalled on a Vivo (2026-09-27: Chrome
/// finished the file but kept it as ".pending" and never offered Install), so
/// the app fetches the APK itself — into its own cache, with progress — and
/// opens the system Install sheet through [InstallChannel]. Android still
/// asks the user to confirm; nothing installs silently.
///
/// Every step reports honestly: a download that stops short, a file that is
/// not the size the server said, or an installer that will not open all
/// surface as [InstallFailure] with a reason the caller can show.
class AppInstaller {
  AppInstaller._();

  static const MethodChannel _channel = MethodChannel('com.farryon/install');

  /// Folder inside the app's cache that the FileProvider exposes
  /// (android/app/src/main/res/xml/file_paths.xml) — nothing else is.
  static const String cacheFolder = 'updates';

  /// Whether Android lets this app open the installer ("Install unknown
  /// apps"). True below Android 8, where there is no such gate.
  static Future<bool> canInstall() async {
    try {
      return await _channel.invokeMethod<bool>('canInstall') ?? false;
    } on MissingPluginException {
      return false;
    }
  }

  /// Opens the system page where the user grants "Install unknown apps".
  static Future<void> openInstallSettings() async {
    try {
      await _channel.invokeMethod<bool>('openInstallSettings');
    } on MissingPluginException {
      // Not Android (or a test host): nothing to open.
    }
  }

  /// Streams [url] into [sink], reporting progress as 0..1 (or null while the
  /// size is unknown). Verifies the byte count against Content-Length: a
  /// truncated download is refused rather than handed to the installer.
  static Future<int> download(
    Uri url,
    IOSink sink, {
    required http.Client client,
    void Function(double? fraction)? onProgress,
  }) async {
    final res = await client.send(http.Request('GET', url));
    if (res.statusCode != 200) {
      throw InstallFailure('The server answered ${res.statusCode}.');
    }
    final expected = res.contentLength;
    var got = 0;
    await for (final chunk in res.stream) {
      sink.add(chunk);
      got += chunk.length;
      onProgress?.call(expected == null || expected == 0 ? null : got / expected);
    }
    if (expected != null && expected > 0 && got != expected) {
      throw InstallFailure(
          'The download stopped early ($got of $expected bytes).');
    }
    if (got == 0) throw const InstallFailure('The download was empty.');
    return got;
  }

  /// The whole thing: download [url] (named for [build]) into the cache and
  /// open the installer for it. Returns the file the installer was given.
  static Future<File> fetchAndInstall(
    Uri url, {
    int? build,
    void Function(double? fraction)? onProgress,
    http.Client? client,
  }) async {
    final dir = Directory('${(await getTemporaryDirectory()).path}/$cacheFolder');
    await dir.create(recursive: true);
    // One file per build (or "latest" when the server refused this build
    // without saying which is current); an older leftover is removed so the
    // cache holds at most the update in hand.
    final name = 'FarryOn-b${build ?? 'latest'}.apk';
    await for (final old in dir.list()) {
      if (old is File && !old.path.endsWith(name)) {
        try {
          await old.delete();
        } catch (_) {}
      }
    }
    final file = File('${dir.path}/$name');
    final c = client ?? http.Client();
    final sink = file.openWrite();
    try {
      await download(url, sink, client: c, onProgress: onProgress);
      await sink.flush();
    } catch (e) {
      await sink.close();
      try {
        await file.delete();
      } catch (_) {}
      if (e is InstallFailure) rethrow;
      throw InstallFailure('The download failed: $e');
    } finally {
      if (client == null) c.close();
    }
    await sink.close();
    try {
      await _channel.invokeMethod<bool>('openInstaller', {'path': file.path});
    } on PlatformException catch (e) {
      throw InstallFailure(e.message ?? "Android's installer would not open.");
    } on MissingPluginException {
      throw const InstallFailure('Installing from the app is not available here.');
    }
    return file;
  }
}

/// Why an in-app update could not be completed, in the user's words.
class InstallFailure implements Exception {
  const InstallFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
