plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.ksp) apply false
}

// Generated files stay out of the source tree: MSJ_ANDROID_BUILD_ROOT when set,
// else the development machine's build volume when it exists, else the
// gitignored in-tree build/ directories.
val buildRoot: File? =
    providers.environmentVariable("MSJ_ANDROID_BUILD_ROOT").orNull?.let(::File)
        ?: File("/workspace/build").takeIf { it.isDirectory }?.resolve("my-smart-journal-feat-android-app")

if (buildRoot != null) {
    allprojects {
        val relative = if (path == ":") "root" else path.removePrefix(":").replace(':', '/')
        layout.buildDirectory.set(buildRoot.resolve("gradle").resolve(relative))
    }
}

// ktlint (with the Compose rule set) over every Kotlin source and build script.
val ktlint: Configuration = configurations.create("ktlint")

dependencies {
    ktlint(libs.ktlint.cli) {
        attributes {
            attribute(Bundling.BUNDLING_ATTRIBUTE, objects.named(Bundling.EXTERNAL))
        }
    }
    ktlint(libs.ktlint.compose.rules)
}

val ktlintPatterns =
    listOf(
        "core/src/**/*.kt",
        "app/src/**/*.kt",
        "**/*.gradle.kts",
        "!**/build/**",
    )

tasks.register<JavaExec>("ktlintCheck") {
    group = "verification"
    description = "Checks Kotlin style with ktlint and the Compose rules."
    classpath = ktlint
    mainClass.set("com.pinterest.ktlint.Main")
    workingDir = projectDir
    args(listOf("--relative") + ktlintPatterns)
}

tasks.register<JavaExec>("ktlintFormat") {
    group = "formatting"
    description = "Rewrites Kotlin sources to the ktlint style where it can."
    classpath = ktlint
    mainClass.set("com.pinterest.ktlint.Main")
    workingDir = projectDir
    jvmArgs("--add-opens=java.base/java.lang=ALL-UNNAMED")
    args(listOf("--relative", "-F") + ktlintPatterns)
}
