import 'package:flutter/material.dart';

import 'core/theme.dart';
import 'features/live/live_screen.dart';

/// Root widget: "Midnight Aurora" theming and the single [LiveScreen] route.
class FarryOnApp extends StatelessWidget {
  const FarryOnApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FarryOn',
      debugShowCheckedModeBanner: false,
      theme: Aurora.theme(),
      home: const LiveScreen(),
    );
  }
}
