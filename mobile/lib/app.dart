import 'package:flutter/material.dart';

import 'features/live/live_screen.dart';

/// Root widget: Material 3 theming and the single [LiveScreen] route.
class FarryOnApp extends StatelessWidget {
  const FarryOnApp({super.key});

  @override
  Widget build(BuildContext context) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF5B6CFF),
      brightness: Brightness.dark,
    );

    return MaterialApp(
      title: 'FarryOn',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: colorScheme,
        scaffoldBackgroundColor: colorScheme.surface,
      ),
      home: const LiveScreen(),
    );
  }
}
