// Pure Kotlin/JVM module: the engine wire-protocol logic (event schema, JSON
// parsing, conversation state reducer, audio framing). No Android dependency,
// so it builds and unit-tests with plain Gradle/JVM tooling - see README.md
// for why that split matters here.
plugins {
    id("org.jetbrains.kotlin.jvm") version "2.0.21"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21"
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")
    testImplementation(kotlin("test-junit5"))
}

tasks.test {
    useJUnitPlatform()
}
