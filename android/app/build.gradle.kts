import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

// Release signing comes from a local properties file outside the repository
// (scripts/make-release-keystore.sh writes one into the build directory):
// storeFile, storePassword, keyAlias, keyPassword. Without it, assembleRelease
// produces an unsigned APK.
val releaseSigning: Properties? =
    providers
        .environmentVariable("MSJ_RELEASE_SIGNING")
        .orNull
        ?.let(::File)
        ?.takeIf { it.isFile }
        ?.let { file -> Properties().apply { file.inputStream().use(::load) } }

android {
    namespace = "dev.msj.journal"
    compileSdk = 37
    // The installed NDK; used only to strip prebuilt native libraries from dependencies.
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "dev.msj.journal"
        minSdk = 28
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        testInstrumentationRunnerArguments["clearPackageData"] = "true"
    }

    signingConfigs {
        if (releaseSigning != null) {
            create("localRelease") {
                storeFile = file(releaseSigning.getProperty("storeFile"))
                storePassword = releaseSigning.getProperty("storePassword")
                keyAlias = releaseSigning.getProperty("keyAlias")
                keyPassword = releaseSigning.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            // Installs beside a release build on the same phone.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("localRelease")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    lint {
        abortOnError = true
        warningsAsErrors = true
        checkAllWarnings = true
        checkDependencies = true
        checkReleaseBuilds = true
        lintConfig = file("lint.xml")
    }

    testOptions {
        execution = "ANDROIDX_TEST_ORCHESTRATOR"
        animationsDisabled = true
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "/META-INF/LICENSE*.md")
    }
}

val strict = providers.gradleProperty("msj.strict").map { it.toBoolean() }.getOrElse(false)

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
        extraWarnings.set(true)
        // Kotlin assert() compiles to JVM assertions: enabled in debuggable builds, removed by R8 in release.
        freeCompilerArgs.add("-Xassertions=jvm")
        // Room's generated Kotlin (KSP) trips these two style diagnostics of extraWarnings, and
        // allWarningsAsErrors cannot be scoped to hand-written sources, so they are off module-wide.
        freeCompilerArgs.addAll(
            "-Xwarning-level=REDUNDANT_VISIBILITY_MODIFIER:disabled",
            "-Xwarning-level=CAN_BE_VAL:disabled",
        )
    }
}

// Warnings fail release compilation (and every compilation in strict mode), never debug work.
tasks.withType<KotlinCompile>().configureEach {
    if (strict || name.contains("Release")) {
        compilerOptions.allWarningsAsErrors.set(true)
    }
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
    arg("room.generateKotlin", "true")
}

dependencies {
    implementation(project(":core"))

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.room.runtime)
    ksp(libs.androidx.room.compiler)
    implementation(libs.androidx.work.runtime)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.compose.ui.tooling.preview)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(kotlin("test-junit"))

    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.uiautomator)
    androidTestImplementation(libs.androidx.work.testing)
    androidTestImplementation(libs.androidx.room.testing)
    androidTestImplementation(libs.kotlinx.coroutines.test)
    androidTestImplementation(kotlin("test-junit"))
    androidTestUtil(libs.androidx.test.orchestrator)
    androidTestUtil(libs.androidx.test.services)
}
