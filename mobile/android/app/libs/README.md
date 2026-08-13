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
- **The build requires it.** This note used to say the app still built without
  the `.aar`, in stub mode. That stopped being true once `HeyCyanGlassesSdk.kt`
  was written against the real SDK: it imports `com.oudmon.*` directly, so
  without the library the Kotlin compile fails with about twenty "overrides
  nothing" and "unresolved reference" errors. Stub mode is a runtime choice
  made in `GlassesChannels.kt`, not a way to build without the library.
- CI cannot use this folder — the `.aar` is not in git. `build-apk.yml`
  fetches it from `/opt/farryon-sdk` on the VPS with the deploy key instead.
  **A new SDK version has to be uploaded there too**, or CI keeps building
  against the old one:

  ```
  scp LIB_GLASSES_SDK-release_*.aar root@<vps>:/opt/farryon-sdk/
  ```

- The interface ↔ vendor API mapping is documented in `GlassesSdk.kt`.
