import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

import 'app.dart';
import 'core/config.dart';
import 'core/config_store.dart';
import 'core/data_cache.dart';
import 'core/outbox.dart';

/// Entry point. Loads persisted settings, then wraps the app in a
/// [ProviderScope] so Riverpod providers are available throughout the tree.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // The real installed version+build (e.g. 1.0.0+4432), before any config
  // is built from it. A failure here must not stop the app from starting.
  try {
    final info = await PackageInfo.fromPlatform();
    AppConfig.installedVersion = '${info.version}+${info.buildNumber}';
  } catch (_) {}
  await ConfigStore.init();
  await DataCache.init();
  await Outbox.init();
  runApp(const ProviderScope(child: FarryOnApp()));
}
