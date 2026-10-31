plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystorePath: String = System.getenv("MAJA_KEYSTORE_PATH")?.trim().orEmpty()

android {
    namespace = "cz.martinpohl.maja"
    compileSdk = 35

    defaultConfig {
        applicationId = "cz.martinpohl.maja"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("MAJA_VERSION_CODE") ?: "1").toIntOrNull() ?: 1
        versionName = System.getenv("MAJA_VERSION_NAME") ?: "0.1.0"
        vectorDrawables.useSupportLibrary = true
    }

    signingConfigs {
        if (keystorePath.isNotEmpty()) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("MAJA_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MAJA_KEY_ALIAS")
                keyPassword = System.getenv("MAJA_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePath.isNotEmpty()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            // Stejné applicationId jako release, ať assetlinks sedí i na ladicí APK
            // podepsané vlastním debug keystorem (otisk pak patří do .env).
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    packaging {
        resources.excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1")
    }
}

tasks.configureEach {
    if (name == "assembleRelease" || name == "bundleRelease" || name == "packageRelease" || name == "signReleaseBundle") {
        doFirst {
            if (keystorePath.isEmpty()) {
                throw GradleException(
                    "Release signing needs MAJA_KEYSTORE_PATH, MAJA_KEYSTORE_PASSWORD, " +
                        "MAJA_KEY_ALIAS and MAJA_KEY_PASSWORD. Never commit the keystore. See RELEASE-SAMSUNG.md.",
                )
            }
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.window:window:1.3.0")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.google.androidbrowserhelper:androidbrowserhelper:2.5.0")
    testImplementation("junit:junit:4.13.2")
}
