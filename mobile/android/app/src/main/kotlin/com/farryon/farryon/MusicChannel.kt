package com.farryon.farryon

import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.provider.MediaStore
import android.util.Log
import android.view.KeyEvent
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * "Play some Arijit Singh" — handed to whatever music app the phone already has.
 *
 * FarryOn owns no player and streams nothing. Two Android mechanisms do all the
 * work here, and both are deliberately permission-free:
 *
 *  - **Starting** something uses [MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH],
 *    the standard "play this" intent that Spotify, YouTube Music and the rest
 *    implement. Android picks the player unless the user named one.
 *  - **Controlling** what is already playing dispatches a media key, which the
 *    system routes to whichever app currently holds the media button session.
 *
 * The second one is fire-and-forget by design: Android returns nothing about
 * whether a player acted on the key, so [mediaKey] reports that the key was
 * SENT, never that music is playing. The assistant is told the same, so it
 * cannot announce a song it has no way of knowing about.
 */
class MusicChannel(private val app: Context) : MethodChannel.MethodCallHandler {
    companion object {
        private const val TAG = "MusicChannel"
        private const val CHANNEL = "com.farryon/music"

        /** Player name from the tool -> Android package. */
        private val PACKAGES = mapOf(
            "spotify" to "com.spotify.music",
            "youtube_music" to "com.google.android.apps.youtube.music",
        )

        /** Command from the tool -> the media key that means it. */
        private val KEYS = mapOf(
            "pause" to KeyEvent.KEYCODE_MEDIA_PAUSE,
            "resume" to KeyEvent.KEYCODE_MEDIA_PLAY,
            "next" to KeyEvent.KEYCODE_MEDIA_NEXT,
            "previous" to KeyEvent.KEYCODE_MEDIA_PREVIOUS,
            "stop" to KeyEvent.KEYCODE_MEDIA_STOP,
        )

        fun register(messenger: BinaryMessenger, app: Context): MusicChannel {
            val handler = MusicChannel(app)
            MethodChannel(messenger, CHANNEL).setMethodCallHandler(handler)
            return handler
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "playFromSearch" -> {
                val query = call.argument<String>("query").orEmpty()
                if (query.isBlank()) {
                    result.error("no_query", "a search query is required", null)
                    return
                }
                result.success(playFromSearch(query, call.argument<String>("app")))
            }
            "mediaKey" -> {
                val command = call.argument<String>("command").orEmpty()
                val key = KEYS[command]
                if (key == null) {
                    result.error("bad_command", "unknown command: $command", null)
                    return
                }
                result.success(mediaKey(key))
            }
            else -> result.notImplemented()
        }
    }

    /** Ask a music app to play [query]. Returns false when nothing can. */
    private fun playFromSearch(query: String, appName: String?): Boolean {
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).apply {
            putExtra(SearchManager.QUERY, query)
            // "unstructured" — we have the user's words, not an artist/album id,
            // and every player treats this focus as "search for it and play".
            putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val named = PACKAGES[appName]
        if (named != null) intent.setPackage(named)
        try {
            app.startActivity(intent)
            return true
        } catch (e: ActivityNotFoundException) {
            // The named app isn't installed (or has no such activity). Fall back
            // to letting Android choose rather than failing the request.
            if (named != null) {
                intent.setPackage(null)
                try {
                    app.startActivity(intent)
                    return true
                } catch (e2: ActivityNotFoundException) {
                    Log.w(TAG, "no music app can handle play-from-search", e2)
                    return false
                }
            }
            Log.w(TAG, "no music app can handle play-from-search", e)
            return false
        }
    }

    /**
     * Send one media key to whichever player holds the session.
     *
     * A key press is a DOWN and an UP; sending only DOWN leaves some players
     * waiting for a long-press that never ends.
     */
    private fun mediaKey(keyCode: Int): Boolean {
        val audio = app.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        if (audio == null) {
            Log.w(TAG, "no AudioManager")
            return false
        }
        return try {
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, keyCode))
            audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, keyCode))
            true
        } catch (e: Exception) {
            Log.w(TAG, "media key $keyCode failed", e)
            false
        }
    }
}
