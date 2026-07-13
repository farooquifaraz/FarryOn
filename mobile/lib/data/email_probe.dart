import 'dart:async';
import 'dart:io';

/// Outcome of a mailbox credential probe.
class EmailProbeResult {
  const EmailProbeResult(this.ok, this.message);
  final bool ok;
  final String message;
}

/// Verify an account's credentials by logging into its IMAP server — using no
/// third-party package: a plain TLS socket speaking just enough IMAP to `LOGIN`
/// then `LOGOUT`. Read-only and side-effect free, so it's safe to run from a
/// "Test connection" button. Returns [EmailProbeResult.ok] on `a1 OK`.
Future<EmailProbeResult> testImapLogin({
  required String host,
  int port = 993,
  required String address,
  required String password,
  Duration timeout = const Duration(seconds: 12),
}) async {
  if (host.trim().isEmpty) {
    return const EmailProbeResult(false, 'No IMAP server for this provider.');
  }
  if (address.trim().isEmpty || password.trim().isEmpty) {
    return const EmailProbeResult(false, 'Enter an address and app password.');
  }
  SecureSocket? socket;
  StreamSubscription<List<int>>? sub;
  try {
    socket = await SecureSocket.connect(host, port, timeout: timeout);

    // Fan the byte stream out into complete CRLF-terminated lines.
    final lines = StreamController<String>.broadcast();
    final buf = StringBuffer();
    sub = socket.listen(
      (data) {
        buf.write(String.fromCharCodes(data));
        var text = buf.toString();
        int i;
        while ((i = text.indexOf('\r\n')) != -1) {
          lines.add(text.substring(0, i));
          text = text.substring(i + 2);
        }
        buf
          ..clear()
          ..write(text);
      },
      onError: (Object e) => lines.addError(e),
      onDone: lines.close,
      cancelOnError: false,
    );

    // Subscribe for the tagged reply BEFORE sending, to avoid a lost-race.
    Future<String> expectTag(String tag) {
      final c = Completer<String>();
      late StreamSubscription<String> s;
      s = lines.stream.listen((line) {
        if (line.toUpperCase().startsWith('${tag.toUpperCase()} ')) {
          if (!c.isCompleted) c.complete(line);
          s.cancel();
        }
      });
      return c.future.timeout(timeout, onTimeout: () {
        s.cancel();
        throw TimeoutException('imap');
      });
    }

    final loginReply = expectTag('a1');
    socket.write('a1 LOGIN ${_quote(address)} ${_quote(password)}\r\n');
    final resp = await loginReply;

    // Best-effort clean logout.
    socket.write('a2 LOGOUT\r\n');

    if (resp.toUpperCase().startsWith('A1 OK')) {
      return const EmailProbeResult(true, 'Connected · inbox reachable');
    }
    final reason = resp
        .replaceFirst(
          RegExp(r'^a1\s+(NO|BAD)\s+', caseSensitive: false),
          '',
        )
        .trim();
    return EmailProbeResult(
        false, reason.isEmpty ? 'Login rejected by the server.' : reason);
  } on SocketException catch (e) {
    return EmailProbeResult(
        false, "Can't reach $host — ${e.osError?.message ?? e.message}");
  } on TimeoutException {
    return const EmailProbeResult(false, 'Timed out reaching the mail server.');
  } on HandshakeException {
    return EmailProbeResult(false, 'TLS handshake failed with $host.');
  } catch (e) {
    return EmailProbeResult(false, 'Connection failed: $e');
  } finally {
    await sub?.cancel();
    try {
      socket?.destroy();
    } catch (_) {}
  }
}

/// IMAP quoted-string: wrap in double quotes, escaping `\` and `"`.
String _quote(String s) =>
    '"${s.replaceAll(r'\', r'\\').replaceAll('"', r'\"')}"';
