/// The "Cloud" preset in Settings must point at the backend we actually run.
///
/// It named the old Render deployment for months after that deployment stopped
/// being where FarryOn lives. Both hosts answer /healthz with the same
/// {"status":"ok"}, so nothing looked broken from the outside — but they are
/// two servers with two databases. A user who installed from the website,
/// created an account, then tapped "Cloud" in Settings was moved to a backend
/// that had never heard of them, and their sign-in stopped working with no
/// error that pointed anywhere near Settings.
///
/// A preset is a promise that the value behind it is the right one. This test
/// is the only thing that checks the promise, because the app connects happily
/// to either.
library;

import 'package:farryon/features/settings/settings_screen.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('the Cloud preset names the live backend', () {
    expect(kCloudHost, 'farryon.izylrn.com');
    expect(kCloudPort, 443);
  });

  test('the Cloud preset is not the retired Render deployment', () {
    // Named explicitly rather than left to the check above: if the live host
    // ever moves again, this is the failure that says *why* it may not move
    // back — Render is a different database, not a different address for the
    // same one.
    expect(kCloudHost, isNot(contains('onrender.com')));
  });
}
