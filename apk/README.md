# FarryOn — built APKs

Installable Android builds of the Flutter app live here. They are **build
artifacts** (git-ignored), not source — regenerate them any time from `mobile/`.

| File | For | Notes |
| --- | --- | --- |
| `FarryOn-Aurora-arm64.apk` | Almost all modern phones (arm64-v8a) | **Install this one.** Midnight Aurora design. |
| `FarryOn-Aurora-arm32.apk` | Older 32-bit phones (armeabi-v7a) | Only if the arm64 build won't install. |

## Install
1. Copy the APK to the phone (Drive / WhatsApp / USB) and tap to install
   (allow "install unknown apps" once).
2. Open **FarryOn** → grant camera + microphone.
3. ⚙️ settings → set your backend: Host = your PC's LAN IP, Port = `8000`,
   Secure (wss) = **OFF** (phone and PC on the same Wi-Fi).

## Rebuild
```bash
cd mobile
flutter build apk --release --split-per-abi
# then copy from build/app/outputs/flutter-apk/ into this folder:
cp build/app/outputs/flutter-apk/app-arm64-v8a-release.apk   ../apk/FarryOn-Aurora-arm64.apk
cp build/app/outputs/flutter-apk/app-armeabi-v7a-release.apk ../apk/FarryOn-Aurora-arm32.apk
```
