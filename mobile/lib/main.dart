import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'core/config_store.dart';
import 'core/data_cache.dart';
import 'core/outbox.dart';

/// Entry point. Loads persisted settings, then wraps the app in a
/// [ProviderScope] so Riverpod providers are available throughout the tree.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ConfigStore.init();
  await DataCache.init();
  await Outbox.init();
  runApp(const ProviderScope(child: FarryOnApp()));
}
