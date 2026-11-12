package dev.msj.journal.audio

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.os.SystemClock
import java.io.File
import java.io.IOException

/**
 * Records voice entries with the platform encoder in a format the server
 * accepts (`_AUDIO_TYPES` in backend/src/msj/api.py): Opus in Ogg on Android 10
 * and newer, AAC in MP4 on Android 9. Not thread-safe: drive it from one thread
 * (the main thread, from the recorder screen's view model).
 */
class VoiceRecorder(
    private val context: Context,
) {
    data class Format(
        /** Bare MIME type sent with the upload. */
        val mimeType: String,
        val extension: String,
        val outputFormat: Int,
        val encoder: Int,
        val sampleRate: Int,
        val bitRate: Int,
    )

    data class Recording(
        val file: File,
        val format: Format,
        val durationMs: Long,
    )

    /** Why recording could not start or continue; the message names the step and the cause. */
    class RecorderException(
        val code: String,
        message: String,
        cause: Throwable? = null,
    ) : Exception(message, cause)

    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var format: Format? = null
    private var startedAt = 0L
    private var pausedAt = 0L
    private var pausedTotal = 0L

    val isActive: Boolean get() = recorder != null
    val isPaused: Boolean get() = recorder != null && pausedAt != 0L

    /** Recorded time so far, excluding pauses. */
    fun elapsedMs(now: Long = SystemClock.elapsedRealtime()): Long {
        if (recorder == null) return 0L
        val end = if (pausedAt != 0L) pausedAt else now
        return (end - startedAt - pausedTotal).coerceAtLeast(0L)
    }

    /** Peak amplitude since the last call, 0..32767. */
    fun maxAmplitude(): Int =
        try {
            recorder?.maxAmplitude ?: 0
        } catch (_: IllegalStateException) {
            0
        }

    /** Starts recording into [file]. [onLimit] runs when the size or duration limit stops it. */
    fun start(
        file: File,
        onLimit: () -> Unit,
        onError: (RecorderException) -> Unit,
    ) {
        assert(recorder == null) { "start() is called once per recording" }
        val chosen = preferredFormat()
        val created = newRecorder()
        try {
            try {
                created.setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            } catch (_: RuntimeException) {
                created.setAudioSource(MediaRecorder.AudioSource.MIC)
            }
            created.setOutputFormat(chosen.outputFormat)
            created.setAudioEncoder(chosen.encoder)
            created.setAudioChannels(1)
            created.setAudioSamplingRate(chosen.sampleRate)
            created.setAudioEncodingBitRate(chosen.bitRate)
            created.setMaxDuration(MAX_DURATION_MS)
            created.setMaxFileSize(MAX_FILE_BYTES)
            created.setOutputFile(file.absolutePath)
            created.setOnInfoListener { _, what, _ ->
                if (what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED ||
                    what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_FILESIZE_REACHED
                ) {
                    onLimit()
                }
            }
            created.setOnErrorListener { _, what, extra ->
                onError(RecorderException("RECORDER_FAILED", "The recorder stopped with error $what (extra $extra)."))
            }
            created.prepare()
            created.start()
        } catch (error: IOException) {
            created.release()
            throw RecorderException("RECORDER_START_FAILED", "Preparing the recorder failed: ${error.message}", error)
        } catch (error: RuntimeException) {
            created.release()
            throw RecorderException(
                "MICROPHONE_UNAVAILABLE",
                "The microphone could not be opened (${error.javaClass.simpleName}). Another app may be using it.",
                error,
            )
        }
        recorder = created
        output = file
        format = chosen
        startedAt = SystemClock.elapsedRealtime()
        pausedAt = 0L
        pausedTotal = 0L
    }

    fun pause() {
        val active = recorder ?: return
        if (pausedAt != 0L) return
        active.pause()
        pausedAt = SystemClock.elapsedRealtime()
    }

    fun resume() {
        val active = recorder ?: return
        if (pausedAt == 0L) return
        active.resume()
        pausedTotal += SystemClock.elapsedRealtime() - pausedAt
        pausedAt = 0L
    }

    /** Finishes the file. A recording too short for the encoder to finish raises RECORDING_TOO_SHORT. */
    fun stop(): Recording {
        val active = checkNotNull(recorder) { "stop() needs an active recording" }
        val file = checkNotNull(output)
        val chosen = checkNotNull(format)
        val duration = elapsedMs()
        try {
            if (pausedAt != 0L) active.resume()
            active.stop()
        } catch (error: RuntimeException) {
            // MediaRecorder.stop() throws when no valid audio was captured.
            discardFile(file)
            throw RecorderException("RECORDING_TOO_SHORT", "The recording has no usable audio; record for at least a second.", error)
        } finally {
            active.release()
            reset()
        }
        if (!file.isFile || file.length() == 0L) {
            throw RecorderException("RECORDING_EMPTY", "The recorder wrote no audio to ${file.name}.")
        }
        return Recording(file, chosen, duration)
    }

    /** Stops and deletes whatever was recorded. */
    fun cancel() {
        val active = recorder ?: return
        val file = output
        try {
            active.stop()
        } catch (_: RuntimeException) {
            // Nothing usable was recorded; the file is deleted below either way.
        } finally {
            active.release()
            reset()
        }
        if (file != null) discardFile(file)
    }

    private fun reset() {
        recorder = null
        output = null
        format = null
        startedAt = 0L
        pausedAt = 0L
        pausedTotal = 0L
    }

    private fun discardFile(file: File) {
        if (file.exists() && !file.delete()) {
            throw RecorderException("RECORDING_DISCARD_FAILED", "The partial recording ${file.name} could not be deleted.")
        }
    }

    @Suppress("DEPRECATION")
    private fun newRecorder(): MediaRecorder =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(context) else MediaRecorder()

    companion object {
        /** About two hours of Opus at 32 kbit/s stays far below the server's 50 MB default upload limit. */
        const val MAX_FILE_BYTES: Long = 45L * 1024 * 1024
        const val MAX_DURATION_MS: Int = 2 * 60 * 60 * 1000

        fun preferredFormat(): Format =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                Format(
                    mimeType = "audio/ogg",
                    extension = "ogg",
                    outputFormat = MediaRecorder.OutputFormat.OGG,
                    encoder = MediaRecorder.AudioEncoder.OPUS,
                    sampleRate = 48_000,
                    bitRate = 32_000,
                )
            } else {
                Format(
                    mimeType = "audio/mp4",
                    extension = "m4a",
                    outputFormat = MediaRecorder.OutputFormat.MPEG_4,
                    encoder = MediaRecorder.AudioEncoder.AAC,
                    sampleRate = 44_100,
                    bitRate = 64_000,
                )
            }
    }
}
