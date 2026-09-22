package com.interpreter.core

import kotlin.test.Test
import kotlin.test.assertEquals

class AudioFramingTest {
    @Test
    fun `frame size matches 30ms at 16kHz mono 16-bit`() {
        assertEquals(960, pcm16FrameSizeBytes(sampleRateHz = 16000, frameMs = 30)) // 480 samples * 2 bytes
    }

    @Test
    fun `slices evenly divisible buffers without padding`() {
        val pcm = ByteArray(1920) { it.toByte() } // exactly 2 frames of 960 bytes
        val frames = framePcm16(pcm, frameBytes = 960)
        assertEquals(2, frames.size)
        assertEquals(960, frames[0].size)
        assertEquals(960, frames[1].size)
    }

    @Test
    fun `zero-pads the final partial frame`() {
        val pcm = ByteArray(1000) { 1 }
        val frames = framePcm16(pcm, frameBytes = 960)
        assertEquals(2, frames.size)
        assertEquals(960, frames[1].size)
        assertEquals(1.toByte(), frames[1][39]) // last real byte (1000 - 960 = 40 real bytes)
        assertEquals(0.toByte(), frames[1][40]) // padding starts here
    }
}
