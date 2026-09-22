package com.interpreter.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Mirrors engine/app/schema.py's EventType - wire values match exactly. */
@Serializable
enum class EventType {
    @SerialName("partial") PARTIAL,
    @SerialName("final") FINAL,
    @SerialName("translation") TRANSLATION,
    @SerialName("audio") AUDIO,
    @SerialName("error") ERROR,
}
