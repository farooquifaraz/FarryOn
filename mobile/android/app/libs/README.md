# Vendor SDK drop-in (HeyCyan / L801 glasses)

Copy the HeyCyan Android SDK library here on your dev machine:

```
From: D:\FarryOn\AI Glasses SDK\HeyCyan_Android_SDK_1.0.2_20250816\...\LIB_GLASSES_SDK-release_3.aar
To:   mobile/android/app/libs/LIB_GLASSES_SDK-release_3.aar
```

- Gradle picks up any `*.aar` in this folder automatically (see
  `app/build.gradle.kts`).
- The `.aar` is **git-ignored on purpose** — it is vendor-licensed binary and
  must not be pushed to the repo.
- Without the `.aar`, the app still builds and the Glasses Lab runs in **stub
  mode** (simulated device).
- Wiring the real SDK = implementing `HeyCyanGlassesSdk` against the
  `GlassesSdk` interface and switching the factory in
  `kotlin/com/farryon/farryon/glasses/GlassesChannels.kt` — that is Sprint 2.
  The interface ↔ vendor API mapping is documented in `GlassesSdk.kt`.
