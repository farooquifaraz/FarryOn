import 'dart:async';

import '../data/data_api.dart';
import 'logger.dart';
import 'outbox.dart';

/// Sends queued offline changes to the server.
///
/// Phase 3 of docs/LOCAL_FIRST_SYNC.md. Every operation here is idempotent by
/// construction (see [Outbox]), so the awkward question — "did that request
/// land before we lost the connection?" — has the same answer either way: send
/// it again.
///
/// Two failures are treated as success, and both are worth being explicit
/// about:
///
/// - **404.** The row is already gone: deleted on another device, or our own
///   request landed and only the reply was lost. Either way the queue asked for
///   it to not exist, and it doesn't. Retrying forever would be a queue that
///   never empties.
/// - **A session that has ended.** [DataApi] signs the user out on a 401 and
///   the sign-out clears the queue, so there is nothing to retry against.
///
/// Everything else — no network, a 500, a timeout — leaves the operation queued
/// and stops the drain. Stopping matters: the usual cause is "the server is
/// unreachable", and grinding through forty more requests to learn that forty
/// more times wastes battery for no information.
class OutboxSync {
  static final _log = Logger('OutboxSync');
  static bool _running = false;

  /// Push everything queued for [userId]. Returns how many were accepted.
  ///
  /// Single-flight: app-resume, a finished screen load and a fresh sign-in can
  /// all ask at once, and two drains racing would send each operation twice.
  /// Harmless — they're idempotent — but pointless.
  static Future<int> drain(DataApi api, int? userId) async {
    if (_running) return 0;
    _running = true;
    try {
      var sent = 0;
      for (final op in Outbox.pending(userId)) {
        if (!await _send(api, op)) break;
        await Outbox.remove(userId, op);
        sent++;
      }
      if (sent > 0) _log.info('outbox: sent $sent queued change(s)');
      return sent;
    } finally {
      _running = false;
    }
  }

  /// True when the operation is settled and can leave the queue.
  static Future<bool> _send(DataApi api, OutboxOp op) async {
    try {
      switch (op.kind) {
        case OutboxKind.deleteNote:
          await api.deleteNote(op.id);
        case OutboxKind.deleteTask:
          await api.deleteTask(op.id);
        case OutboxKind.setTaskDone:
          await api.setTaskDone(op.id, op.done);
      }
      return true;
    } on NotFoundException {
      // Already gone — the queue got what it wanted.
      _log.info('outbox: ${op.kind.name} ${op.id} was already applied');
      return true;
    } on SessionExpiredException {
      // Sign-out clears the queue; there is nothing left to send.
      return false;
    } catch (e) {
      _log.warn('outbox: ${op.kind.name} ${op.id} deferred — $e');
      return false;
    }
  }
}
