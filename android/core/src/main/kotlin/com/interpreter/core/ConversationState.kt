package com.interpreter.core

/** One conversational turn's accumulated state, built up from FINAL/TRANSLATION events. */
data class Turn(
    val turnId: String,
    val sourceLang: String = "",
    val sourceText: String = "",
    val targetLang: String = "",
    val translations: List<String> = emptyList(),
)

/**
 * Pure, immutable reducer over the engine's PipelineEvent stream. One instance
 * per conversation/connection; call `reduce(event)` for every event received
 * and render from the returned state. No Android/UI dependency, so this is
 * fully unit-testable on the JVM (see ConversationStateTest) - the ViewModel
 * in the app module is a thin wrapper that calls this and exposes the result
 * as a StateFlow.
 */
data class ConversationState(
    val partialText: String = "",
    val partialLang: String = "",
    val turns: List<Turn> = emptyList(),
) {
    fun reduce(event: PipelineEvent): ConversationState = when (event.type) {
        EventType.PARTIAL -> copy(partialText = event.text, partialLang = event.lang)

        EventType.FINAL -> copy(
            partialText = "",
            partialLang = "",
            turns = turns + Turn(turnId = event.turnId, sourceLang = event.lang, sourceText = event.text),
        )

        EventType.TRANSLATION -> updateLastTurn(event.turnId) { turn ->
            turn.copy(targetLang = event.lang, translations = turn.translations + event.text)
        }

        // AUDIO carries playback bytes, not display text - the player consumes
        // it directly from the raw event stream, not from this reducer.
        EventType.AUDIO, EventType.ERROR -> this
    }

    private inline fun updateLastTurn(turnId: String, transform: (Turn) -> Turn): ConversationState {
        val index = turns.indexOfLast { it.turnId == turnId }
        if (index == -1) return this
        val updated = turns.toMutableList()
        updated[index] = transform(updated[index])
        return copy(turns = updated)
    }
}
