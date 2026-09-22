package com.interpreter.core

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNamingStrategy

/**
 * Shared JSON config for the engine's wire events. The engine (Python/pydantic)
 * emits snake_case keys (`turn_id`, `latency_ms`, ...); Kotlin fields stay
 * idiomatic camelCase and this naming strategy bridges the two, so no
 * per-field @SerialName annotations are needed.
 */
@OptIn(ExperimentalSerializationApi::class)
val EngineJson: Json = Json {
    namingStrategy = JsonNamingStrategy.SnakeCase
    ignoreUnknownKeys = true
    isLenient = true
}
