// Android application module. Needs the Android SDK (platforms/build-tools
// from dl.google.com) to build - open this project in Android Studio, which
// will fetch the SDK automatically. Not buildable in a network-sandboxed
// environment without Google's Maven repo - see README.md "Building".
plugins {
    id("com.android.application") version "8.5.2"
    id("org.jetbrains.kotlin.android") version "2.0.21"
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21"
}

android {
    namespace = "com.interpreter.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.interpreter.app"
        // minSdk 31 (Android 12) is required for AudioManager.setCommunicationDevice /
        // availableCommunicationDevices, which is how we prefer the Buds 3 Pro's
        // LE Audio (LC3) route over classic Bluetooth - see audio/BluetoothAudioRouting.kt.
        minSdk = 31
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(project(":core"))

    implementation(platform("androidx.compose:compose-bom:2024.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.core:core-ktx:1.13.1")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Standalone mode: on-device translation. Free, Apache-2.0, no server -
    // android.speech.SpeechRecognizer and android.speech.tts.TextToSpeech
    // used alongside it are both part of the Android SDK, no dependency needed.
    implementation("com.google.mlkit:translate:17.0.3")
}
