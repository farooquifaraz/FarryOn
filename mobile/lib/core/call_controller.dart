import 'dart:async';

import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

import 'logger.dart';

/// What happened when we tried to put a call through.
enum CallOutcome {
  /// The phone is dialling now.
  called,

  /// The user hasn't granted the call permission — the caller should open the
  /// dialer instead, and say so.
  noPermission,

  /// The platform refused (no dialler app, or an unexpected failure).
  failed,
}

/// Places a real call over the native `com.farryon/call` channel.
///
/// The permission is asked for HERE rather than at startup: a person who never
/// asks Farry to call anyone should never see a call-permission prompt, and one
/// who does gets it in the moment they'd expect it.
class CallController {
  CallController._();

  static const MethodChannel _channel = MethodChannel('com.farryon/call');
  static final _log = Logger('CallController');

  static final StreamController<bool> _callAudio =
      StreamController<bool>.broadcast();
  static bool _listening = false;

  /// True while a phone call owns the audio, false when it gives it back.
  ///
  /// A live session and a phone call cannot share the one microphone, so
  /// whoever is running the session has to stand down for the length of a call
  /// and pick up afterwards.
  static Stream<bool> get callAudio {
    if (!_listening) {
      _listening = true;
      _channel.setMethodCallHandler((call) async {
        if (call.method != 'callAudio') return null;
        final inCall = (call.arguments as Map?)?['inCall'] == true;
        _log.info('phone call ${inCall ? 'started' : 'ended'}');
        _callAudio.add(inCall);
        return null;
      });
    }
    return _callAudio.stream;
  }

  /// Dial [number] (E.164, with or without the leading +).
  ///
  /// Never throws: a refusal or a platform failure comes back as an outcome so
  /// the caller can fall back to the dialer rather than leaving the user with
  /// a call they were told was happening and isn't.
  static Future<CallOutcome> call(String number) async {
    if (number.trim().isEmpty) return CallOutcome.failed;
    try {
      // Permission.phone is CALL_PHONE on Android. Asking every time is safe:
      // once granted, this resolves immediately without a prompt.
      final status = await Permission.phone.request();
      if (!status.isGranted) {
        _log.info('call permission not granted ($status)');
        return CallOutcome.noPermission;
      }
      final res = await _channel.invokeMethod<String>('placeCall', {
        'number': number,
      });
      switch (res) {
        case 'called':
          _log.info('placed a call');
          return CallOutcome.called;
        case 'no_permission':
          return CallOutcome.noPermission;
        default:
          return CallOutcome.failed;
      }
    } catch (e) {
      _log.warn('place call failed: $e');
      return CallOutcome.failed;
    }
  }
}
