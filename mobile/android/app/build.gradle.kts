plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.farryon.farryon"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        // Required by flutter_local_notifications (uses java.time on older APIs).
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.farryon.farryon"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
            // R8 shrinking is ON to keep the APK small (the unshrunk DEX was
            // ~12 MB). flutter_local_notifications persists scheduled reminders
            // via Gson generic TypeTokens, so R8 MUST preserve generic
            // signatures or the receiver crashes with "Missing type parameter"
            // and the reminder never fires — the keep rules in proguard-rules.pro
            // (`-keepattributes Signature`, `com.dexterous.**`, Gson) guard that.
            // Re-verify reminders on a device after changing these settings.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
    // HeyCyan glasses SDK: drop the vendor LIB_GLASSES_SDK-release_*.aar into
    // app/libs/ (git-ignored) and it is picked up automatically. With no .aar
    // present the Glasses Lab runs on its built-in stub SDK, so clean clones
    // and CI build fine without the vendor binary.
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
    // The vendor .aar downloads media via androidnetworking, which needs
    // OkHttp (+ gson per the vendor integration guide §2.1) at runtime but
    // bundles neither — without these, WiFi sync crashes with
    // NoClassDefFoundError: okhttp3.MediaType (hit on-device 2026-07-06).
    implementation("com.squareup.okhttp3:okhttp:4.9.3")
    implementation("com.google.code.gson:gson:2.8.9")
}
