import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';

/// Entry point. Wraps the app in a [ProviderScope] so Riverpod providers are
/// available throughout the widget tree.
void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: FarryOnApp()));
}
