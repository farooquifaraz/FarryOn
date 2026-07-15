# Safety-net rules for release shrinking. Currently minification is disabled
# (see build.gradle.kts), but if it is ever re-enabled these keep the
# flutter_local_notifications + Gson path working so scheduled reminders fire.

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
