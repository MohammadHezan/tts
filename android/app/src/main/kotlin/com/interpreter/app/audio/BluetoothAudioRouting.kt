package com.interpreter.app.audio

import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import androidx.annotation.RequiresApi

/**
 * Prefers a connected LE Audio (LC3) Bluetooth device - the Buds 3 Pro's
 * modern codec - for both capture and playback, over classic Bluetooth/SCO.
 *
 * This is the single biggest software-controllable lever for matching
 * Samsung's Interpreter-mode latency: LC3 round-trips at ~50-100ms versus
 * ~120-200ms on classic AAC Bluetooth (see README "How Samsung does it").
 * Requires API 31+ (this app's minSdk); silently no-ops below that or if no
 * LE Audio device is connected, falling back to whatever the OS picks.
 */
object BluetoothAudioRouting {

    @RequiresApi(Build.VERSION_CODES.S)
    fun preferLeAudioDevice(audioManager: AudioManager): Boolean {
        val leAudioDevice = audioManager.availableCommunicationDevices
            .firstOrNull { it.type == AudioDeviceInfo.TYPE_BLE_HEADSET }
            ?: return false
        return audioManager.setCommunicationDevice(leAudioDevice)
    }

    @RequiresApi(Build.VERSION_CODES.S)
    fun clearPreferredDevice(audioManager: AudioManager) {
        audioManager.clearCommunicationDevice()
    }
}
