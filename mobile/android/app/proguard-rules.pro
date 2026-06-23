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
