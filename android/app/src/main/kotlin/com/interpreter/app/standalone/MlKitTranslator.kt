package com.interpreter.app.standalone

import com.google.android.gms.tasks.Task
import com.google.mlkit.common.model.DownloadConditions
import com.google.mlkit.nl.translate.TranslateLanguage
import com.google.mlkit.nl.translate.Translation
import com.google.mlkit.nl.translate.Translator
import com.google.mlkit.nl.translate.TranslatorOptions
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * On-device EN<->AR translation via ML Kit Translate (free, Apache-2.0,
 * Google's own on-device NMT). Fully offline after a one-time per-language
 * model download (~30MB, needs internet once - see README). Generic
 * translation quality, not tuned with the engine's glossary/context memory -
 * that trade-off is the price of needing no server at all.
 */
class MlKitTranslator {

    private val translators = mutableMapOf<Pair<String, String>, Translator>()

    private fun translatorFor(sourceLanguage: String, targetLanguage: String): Translator {
        val key = sourceLanguage to targetLanguage
        return translators.getOrPut(key) {
            val options = TranslatorOptions.Builder()
                .setSourceLanguage(sourceLanguage)
                .setTargetLanguage(targetLanguage)
                .build()
            Translation.getClient(options)
        }
    }

    /** Downloads the model for this language pair now, e.g. during onboarding. */
    suspend fun ensureModelDownloaded(sourceLanguage: String, targetLanguage: String) {
        translatorFor(sourceLanguage, targetLanguage)
            .downloadModelIfNeeded(DownloadConditions.Builder().build())
            .await()
    }

    suspend fun translate(text: String, sourceLanguage: String, targetLanguage: String): String {
        val translator = translatorFor(sourceLanguage, targetLanguage)
        translator.downloadModelIfNeeded(DownloadConditions.Builder().build()).await()
        return translator.translate(text).await()
    }

    fun close() {
        translators.values.forEach { it.close() }
        translators.clear()
    }

    companion object {
        const val ENGLISH = TranslateLanguage.ENGLISH
        const val ARABIC = TranslateLanguage.ARABIC
    }
}

private suspend fun <T> Task<T>.await(): T = suspendCancellableCoroutine { continuation ->
    addOnSuccessListener { result -> continuation.resume(result) }
    addOnFailureListener { exception -> continuation.resumeWithException(exception) }
}
