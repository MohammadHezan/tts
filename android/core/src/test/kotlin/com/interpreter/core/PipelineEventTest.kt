package com.interpreter.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class PipelineEventTest {
    @Test
    fun `parses snake_case wire JSON into camelCase fields`() {
        val json = """
            {"type":"final","lang":"en","text":"Good afternoon.","turn_id":"abc123","seq":3,
             "latency_ms":812.5,"is_final_segment":true,"audio":null,"audio_sample_rate":null,
             "timestamp":1234.5,"error":null}
        """.trimIndent()

        val event = PipelineEvent.fromJson(json)

        assertEquals(EventType.FINAL, event.type)
        assertEquals("en", event.lang)
        assertEquals("Good afternoon.", event.text)
        assertEquals("abc123", event.turnId)
        assertEquals(3, event.seq)
        assertEquals(812.5, event.latencyMs)
        assertEquals(true, event.isFinalSegment)
        assertNull(event.audio)
    }

    @Test
    fun `parses audio event with base64 payload`() {
        val json = """{"type":"audio","lang":"ar","text":"مرحبا","turn_id":"t1","audio":"AAEC","audio_sample_rate":16000}"""

        val event = PipelineEvent.fromJson(json)

        assertEquals(EventType.AUDIO, event.type)
        assertEquals("AAEC", event.audio)
        assertEquals(16000, event.audioSampleRate)
    }

    @Test
    fun `defaults apply for optional fields`() {
        val json = """{"type":"partial","lang":"en","turn_id":"t1"}"""

        val event = PipelineEvent.fromJson(json)

        assertEquals("", event.text)
        assertEquals(0, event.seq)
        assertEquals(false, event.isFinalSegment)
        assertNull(event.error)
    }
}
