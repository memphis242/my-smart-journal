import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// Platform-free logic: the API client, error model, NDJSON reader, date ranges
// and the outbox state machine. Plain JVM, so its tests run in seconds.
plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

val strict = providers.gradleProperty("msj.strict").map { it.toBoolean() }.getOrElse(false)

kotlin {
    explicitApi()
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
        allWarningsAsErrors.set(strict)
        extraWarnings.set(true)
        // Kotlin assert() compiles to JVM assertions: on in tests and debug, free in release.
        freeCompilerArgs.addAll("-Xassertions=jvm", "-Xjdk-release=17")
    }
}

dependencies {
    api(libs.kotlinx.coroutines.core)
    api(libs.kotlinx.serialization.json)
    api(libs.okhttp)
    api(libs.okio)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.okhttp.tls)
    testImplementation(kotlin("test-junit"))
}

tasks.withType<Test>().configureEach {
    enableAssertions = true
    maxParallelForks = (Runtime.getRuntime().availableProcessors() / 2).coerceAtLeast(1)
    testLogging {
        events("failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
