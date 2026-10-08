plugins {
    id("com.android.application")
}

android {
    namespace = "tw.avianjay.autocoin"

    compileSdk {
        version = release(36)
    }

    defaultConfig {
        applicationId = "tw.avianjay.autocoin"
        minSdk { version = release(30) }
        targetSdk { version = release(36) }
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
}
