package com.interpreter.app.captionbridge

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.graphics.Rect
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Watches for on-screen text changes in Zoom/Google Meet (declared in
 * res/xml/caption_accessibility_service_config.xml, which is what actually
 * restricts this to only those two apps - AccessibilityService is the same
 * OS-sanctioned API screen readers use to read on-screen text; this reads
 * nothing outside the two watched apps and does not touch audio at all.
 *
 * Best-effort by necessity: Zoom/Meet don't publish a stable "this is the
 * caption view" identifier, so [findLikelyCaptionText] uses a heuristic
 * (view-id keyword match, else the longest text in the bottom half of the
 * screen, where captions are conventionally overlaid) that could not be
 * verified against a live call in the environment this was built in - see
 * CaptionBridgeScreen's "raw detected text" debug view to confirm/tune this
 * against your actual device.
 */
class CaptionAccessibilityService : AccessibilityService() {

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val packageName = event.packageName?.toString() ?: return
        if (packageName !in WATCHED_PACKAGES) return

        val root = rootInActiveWindow ?: return
        val text = findLikelyCaptionText(root)
        if (!text.isNullOrBlank()) {
            _captionText.tryEmit(text)
        }
    }

    override fun onInterrupt() = Unit

    private fun findLikelyCaptionText(root: AccessibilityNodeInfo): String? {
        val screenHeight = resources.displayMetrics.heightPixels
        var idMatch: String? = null
        var bestFallback: String? = null
        var bestFallbackLength = 0
        val bounds = Rect()

        fun visit(node: AccessibilityNodeInfo) {
            val text = node.text?.toString()?.trim()
            if (!text.isNullOrEmpty()) {
                val resId = node.viewIdResourceName?.lowercase().orEmpty()
                if (idMatch == null && CAPTION_ID_HINTS.any { resId.contains(it) }) {
                    idMatch = text
                }
                node.getBoundsInScreen(bounds)
                val inLowerHalf = bounds.top > screenHeight / 2
                if (inLowerHalf && text.length > bestFallbackLength) {
                    bestFallback = text
                    bestFallbackLength = text.length
                }
            }
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let(::visit)
            }
        }
        visit(root)
        return idMatch ?: bestFallback
    }

    companion object {
        private val WATCHED_PACKAGES = setOf("us.zoom.videomeetings", "com.google.android.apps.meetings")
        private val CAPTION_ID_HINTS = listOf("caption", "subtitle", "transcript", "cc_text")

        private val _captionText = MutableSharedFlow<String>(replay = 1, extraBufferCapacity = 8)

        /** Latest on-screen caption text detected in a watched app, as it changes. */
        val captionText: SharedFlow<String> = _captionText.asSharedFlow()

        /**
         * Accessibility services can't be enabled programmatically - the user
         * must turn this on manually in system Settings. This just checks
         * whether they already have, so the UI can show real status instead
         * of always pointing at Settings.
         */
        fun isEnabled(context: Context): Boolean {
            val expected = ComponentName(context, CaptionAccessibilityService::class.java).flattenToString()
            val enabledServices = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: return false
            val splitter = TextUtils.SimpleStringSplitter(':')
            splitter.setString(enabledServices)
            while (splitter.hasNext()) {
                if (splitter.next().equals(expected, ignoreCase = true)) return true
            }
            return false
        }
    }
}
