# Release shrinking rules. Minification IS enabled (see build.gradle.kts
# isMinifyEnabled = true), so every third-party SDK that R8 can't see used
# statically — anything reached via reflection, JNI, or BLE/native callbacks —
# needs an explicit -keep here or R8 strips it and the feature silently dies.

# flutter_local_notifications uses Gson with generic TypeTokens to persist and
# restore scheduled notifications. R8 must preserve generic signatures or the
# receiver crashes with "Missing type parameter" and the reminder never shows.
-keep class com.dexterous.** { *; }
-keepattributes Signature
-keepattributes *Annotation*
-keepattributes InnerClasses
-keepattributes EnclosingMethod

# Gson
-keep class com.google.gson.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken
-keepclassmembers,allowobfuscation class * {
  @com.google.gson.annotations.SerializedName <fields>;
}
-dontwarn com.google.gson.**

# Flutter embedding (defensive).
-keep class io.flutter.** { *; }

# Vendor smart-glasses SDK (the Oudmon/HeyCyan L801 .aar in app/libs/). The app
# reaches it only through reflection + BLE/native callbacks, so R8 can't see it
# is used and strips com.oudmon.** entirely in release. When that happens the
# GlassesSdk factory catches the boot failure and silently falls back to the
# built-in StubGlassesSdk — so glasses "connect" and "capture" appear to work
# but every frame is a synthetic "L801 STUB FRAME", never the real camera.
# Keep the vendor packages (and its androidnetworking media-download dep) so the
# real SDK loads in release the same way it does in a debug build.
-keep class com.oudmon.** { *; }
-keep class com.jieli.** { *; }
-keep class com.androidnetworking.** { *; }
-dontwarn com.oudmon.**
-dontwarn com.jieli.**
-dontwarn com.androidnetworking.**

# HeyCyanGlassesSdk reaches LocalBroadcastManager REFLECTIVELY
# (Class.forName + getMethod("getInstance", Context) — see HeyCyanGlassesSdk.kt,
# done this way to avoid a gradle dep change). R8 can't see a string-based
# reflective reference, so it obfuscates the class and renames getInstance ->
# the reflective lookup throws NoSuchMethodException, the vendor SDK constructor
# fails, and the factory silently falls back to the stub (a real "L801 STUB
# FRAME" bug found 2026-07-26). Keep the class + method names intact.
-keep class androidx.localbroadcastmanager.** { *; }
-dontwarn androidx.localbroadcastmanager.**

# Flutter's embedding references Google Play Core (deferred components / split
# install) classes that we don't bundle — we ship a plain APK with no dynamic
# feature modules. Suppress the resulting R8 "missing class" errors.
-dontwarn com.google.android.play.core.**
-keep class io.flutter.embedding.engine.deferredcomponents.** { *; }
-keep class io.flutter.embedding.android.FlutterPlayStoreSplitApplication { *; }

# OkHttp (pulled in transitively) compiles against three OPTIONAL TLS providers
# — BouncyCastle, Conscrypt and OpenJSSE — and picks whichever is present at
# runtime. We bundle none of them (the platform's TLS is used), so R8 sees the
# references as missing classes and fails minifyRelease outright. They are only
# ever touched behind an availability check, so suppressing the warnings is safe
# — this is the exact rule set R8 itself emits in missing_rules.txt.
-dontwarn org.bouncycastle.jsse.BCSSLParameters
-dontwarn org.bouncycastle.jsse.BCSSLSocket
-dontwarn org.bouncycastle.jsse.provider.BouncyCastleJsseProvider
-dontwarn org.conscrypt.Conscrypt$Version
-dontwarn org.conscrypt.Conscrypt
-dontwarn org.conscrypt.ConscryptHostnameVerifier
-dontwarn org.openjsse.javax.net.ssl.SSLParameters
-dontwarn org.openjsse.javax.net.ssl.SSLSocket
-dontwarn org.openjsse.net.ssl.OpenJSSE
