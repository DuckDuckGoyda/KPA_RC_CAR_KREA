// AGP 9 compiles Kotlin itself ("built-in Kotlin"), so no separate Kotlin plugin is needed.
plugins {
    id("com.android.application")
}

android {
    namespace = "com.rccar.fakecar"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.rccar.fakecar"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    // Only for the JVM unit tests; the app itself has no third-party dependencies.
    testImplementation("junit:junit:4.13.2")
}
