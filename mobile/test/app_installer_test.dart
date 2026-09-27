/// The in-app update download: whole or refused, never a half file to the
/// installer (Vivo 2026-09-27: the browser route stalled at 100%).
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:farryon/core/app_installer.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Client _serving(List<int> bytes, {int? contentLength, int status = 200}) =>
    MockClient.streaming((req, _) async {
      final stream = Stream<List<int>>.fromIterable([
        bytes.sublist(0, bytes.length ~/ 2),
        bytes.sublist(bytes.length ~/ 2),
      ]);
      return http.StreamedResponse(
        stream,
        status,
        contentLength: contentLength,
        headers: {'content-type': 'application/vnd.android.package-archive'},
      );
    });

Future<(List<int>, List<double?>)> _run(http.Client client) async {
  final got = <int>[];
  final progress = <double?>[];
  final sink = _ListSink(got);
  await AppInstaller.download(
    Uri.parse('https://farryon.test/download/arm64'),
    sink,
    client: client,
    onProgress: progress.add,
  );
  return (got, progress);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('a complete download reports progress up to 1', () async {
    final bytes = List<int>.generate(1000, (i) => i % 256);
    final (got, progress) = await _run(_serving(bytes, contentLength: 1000));
    expect(got, bytes);
    expect(progress.last, 1.0);
    expect(progress.first, 0.5);
  });

  test('a download that stops short is refused', () async {
    final bytes = List<int>.generate(1000, (i) => i % 256);
    expect(
      () => _run(_serving(bytes, contentLength: 2000)),
      throwsA(isA<InstallFailure>()
          .having((e) => e.message, 'message', contains('stopped early'))),
    );
  });

  test('a server error is refused with its status', () async {
    expect(
      () => _run(_serving([1, 2, 3], contentLength: 3, status: 503)),
      throwsA(isA<InstallFailure>()
          .having((e) => e.message, 'message', contains('503'))),
    );
  });

  test('an unknown size still streams, with unknown progress', () async {
    final bytes = List<int>.generate(10, (i) => i);
    final (got, progress) = await _run(_serving(bytes));
    expect(got, bytes);
    expect(progress, everyElement(isNull));
  });

  test('canInstall reads the platform, and is false without it', () async {
    expect(await AppInstaller.canInstall(), isFalse, reason: 'no plugin here');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('com.farryon/install'),
      (call) async => call.method == 'canInstall' ? true : null,
    );
    expect(await AppInstaller.canInstall(), isTrue);
  });
}

/// An [IOSink] that keeps what it is given, for tests without a file.
class _ListSink implements IOSink {
  _ListSink(this.out);
  final List<int> out;

  @override
  void add(List<int> data) => out.addAll(data);

  @override
  Future<void> close() async {}
  @override
  Future<void> flush() async {}

  @override
  Future<void> addStream(Stream<List<int>> stream) async {
    await for (final c in stream) {
      add(c);
    }
  }

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}
  @override
  Future<void> get done async {}
  @override
  Encoding encoding = utf8;
  @override
  void write(Object? object) {}
  @override
  void writeAll(Iterable<dynamic> objects, [String separator = '']) {}
  @override
  void writeCharCode(int charCode) {}
  @override
  void writeln([Object? object = '']) {}
}
