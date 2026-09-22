package com.interpreter.core

import kotlin.test.Test
import kotlin.test.assertEquals

class ConversationStateTest {
    private fun event(type: EventType, lang: String, text: String, turnId: String = "t1") =
        PipelineEvent(type = type, lang = lang, text = text, turnId = turnId)

    @Test
    fun `partial updates the live caption and is cleared on final`() {
        var state = ConversationState()
        state = state.reduce(event(EventType.PARTIAL, "en", "Good aft"))
        assertEquals("Good aft", state.partialText)

        state = state.reduce(event(EventType.FINAL, "en", "Good afternoon."))
        assertEquals("", state.partialText)
        assertEquals(1, state.turns.size)
        assertEquals("Good afternoon.", state.turns[0].sourceText)
    }

    @Test
    fun `translation events attach to the matching turn`() {
        var state = ConversationState()
        state = state.reduce(event(EventType.FINAL, "en", "Good afternoon.", turnId = "t1"))
        state = state.reduce(event(EventType.TRANSLATION, "ar", "مساء الخير", turnId = "t1"))

        assertEquals(1, state.turns.size)
        assertEquals(listOf("مساء الخير"), state.turns[0].translations)
        assertEquals("ar", state.turns[0].targetLang)
    }

    @Test
    fun `multiple sentences in one turn accumulate in order`() {
        var state = ConversationState()
        state = state.reduce(event(EventType.FINAL, "en", "First. Second.", turnId = "t1"))
        state = state.reduce(event(EventType.TRANSLATION, "ar", "أولا", turnId = "t1"))
        state = state.reduce(event(EventType.TRANSLATION, "ar", "ثانيا", turnId = "t1"))

        assertEquals(listOf("أولا", "ثانيا"), state.turns[0].translations)
    }

    @Test
    fun `turns from separate utterances are kept independent`() {
        var state = ConversationState()
        state = state.reduce(event(EventType.FINAL, "en", "Turn one.", turnId = "t1"))
        state = state.reduce(event(EventType.FINAL, "ar", "دور اثنان", turnId = "t2"))

        assertEquals(2, state.turns.size)
        assertEquals("t1", state.turns[0].turnId)
        assertEquals("t2", state.turns[1].turnId)
    }

    @Test
    fun `audio and error events do not change conversation text state`() {
        var state = ConversationState()
        state = state.reduce(event(EventType.FINAL, "en", "Hello.", turnId = "t1"))
        val beforeAudio = state
        state = state.reduce(PipelineEvent(type = EventType.AUDIO, lang = "ar", turnId = "t1", audio = "AAEC"))
        assertEquals(beforeAudio, state)
    }
}
