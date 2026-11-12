package dev.msj.journal.ui.capture

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.audio.VoiceRecorder
import dev.msj.journal.core.dates.isoDate
import dev.msj.journal.ui.UserError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File
import java.util.UUID

sealed interface RecorderPhase {
    /** Waiting for the microphone permission (or for the user to start). */
    data object Ready : RecorderPhase

    data class Recording(
        val elapsedMs: Long,
        /** Recent input levels, 0..1, oldest first. */
        val levels: List<Float>,
        val paused: Boolean,
        /** Paused because the app left the screen; Android silences the microphone in the background. */
        val pausedByBackground: Boolean,
    ) : RecorderPhase

    data class Review(
        val durationMs: Long,
        val sizeBytes: Long,
    ) : RecorderPhase

    data object Saved : RecorderPhase
}

data class RecorderUi(
    val phase: RecorderPhase = RecorderPhase.Ready,
    val title: String = "",
    val tags: String = "",
    val saving: Boolean = false,
    val error: UserError? = null,
    val permissionDenied: Boolean = false,
)

/**
 * A voice entry: record, review, save to the outbox. The recording goes to the
 * cache first and moves to the outbox only on Save; a stopped recording that
 * was not saved yet survives process death through the saved state.
 */
class RecorderViewModel(
    private val services: AppServices,
    private val handle: SavedStateHandle,
) : ViewModel() {
    private val recorder = VoiceRecorder(services.context)
    private val captureId: String = handle[KEY_ID] ?: UUID.randomUUID().toString().also { handle[KEY_ID] = it }
    private val format = VoiceRecorder.preferredFormat()
    private val recordingDir = File(services.context.cacheDir, "recordings")
    private val file = File(recordingDir, "$captureId.${format.extension}")
    private var ticker: Job? = null

    private val mutableUi = MutableStateFlow(restore())
    val ui: StateFlow<RecorderUi> = mutableUi.asStateFlow()

    private fun restore(): RecorderUi {
        val duration: Long? = handle[KEY_DURATION]
        return if (duration != null && file.isFile) {
            RecorderUi(
                phase = RecorderPhase.Review(duration, file.length()),
                title = handle[KEY_TITLE] ?: "",
                tags = handle[KEY_TAGS] ?: "",
            )
        } else {
            removeStaleRecordings()
            RecorderUi()
        }
    }

    fun onPermissionResult(granted: Boolean) {
        if (granted) {
            mutableUi.update { it.copy(permissionDenied = false, error = null) }
            start()
        } else {
            mutableUi.update {
                it.copy(
                    permissionDenied = true,
                    error =
                        UserError(
                            code = "MICROPHONE_PERMISSION_DENIED",
                            message = "Recording needs access to the microphone.",
                            hint =
                                "Allow it when asked, or in Settings > Apps > Journal > Permissions > Microphone. " +
                                    "Writing an entry works without it.",
                            details = emptyList(),
                        ),
                )
            }
        }
    }

    fun start() {
        if (recorder.isActive || mutableUi.value.phase !is RecorderPhase.Ready) return
        if (!recordingDir.isDirectory && !recordingDir.mkdirs()) {
            mutableUi.update {
                it.copy(
                    error =
                        UserError(
                            "RECORDING_STORAGE_FAILED",
                            "The app could not create its recordings folder.",
                            "Free some storage space, then try again.",
                            emptyList(),
                        ),
                )
            }
            return
        }
        try {
            recorder.start(
                file,
                onLimit = { stop() },
                onError = { error -> fail(error) },
            )
        } catch (error: VoiceRecorder.RecorderException) {
            fail(error)
            return
        }
        mutableUi.update {
            it.copy(
                phase = RecorderPhase.Recording(0L, emptyList(), paused = false, pausedByBackground = false),
                error = null,
            )
        }
        ticker =
            viewModelScope.launch {
                while (isActive) {
                    val level = (recorder.maxAmplitude() / 32_767f).coerceIn(0f, 1f)
                    mutableUi.update { current ->
                        val phase = current.phase as? RecorderPhase.Recording ?: return@update current
                        val levels = if (phase.paused) phase.levels else (phase.levels + level).takeLast(LEVELS)
                        current.copy(phase = phase.copy(elapsedMs = recorder.elapsedMs(), levels = levels))
                    }
                    delay(TICK_MS)
                }
            }
    }

    fun togglePause() {
        val phase = mutableUi.value.phase as? RecorderPhase.Recording ?: return
        if (phase.paused) recorder.resume() else recorder.pause()
        mutableUi.update { it.copy(phase = phase.copy(paused = !phase.paused, pausedByBackground = false)) }
    }

    /** The app left the foreground: pause, because a background app records silence. */
    fun onBackground() {
        val phase = mutableUi.value.phase as? RecorderPhase.Recording ?: return
        if (phase.paused) return
        recorder.pause()
        mutableUi.update { it.copy(phase = phase.copy(paused = true, pausedByBackground = true)) }
    }

    fun stop() {
        if (!recorder.isActive) return
        ticker?.cancel()
        val recording =
            try {
                recorder.stop()
            } catch (error: VoiceRecorder.RecorderException) {
                fail(error)
                mutableUi.update { it.copy(phase = RecorderPhase.Ready) }
                return
            }
        handle[KEY_DURATION] = recording.durationMs
        mutableUi.update { it.copy(phase = RecorderPhase.Review(recording.durationMs, recording.file.length())) }
    }

    fun onTitle(value: String) {
        handle[KEY_TITLE] = value
        mutableUi.update { it.copy(title = value) }
    }

    fun onTags(value: String) {
        handle[KEY_TAGS] = value
        mutableUi.update { it.copy(tags = value) }
    }

    fun save() {
        val current = mutableUi.value
        val review = current.phase as? RecorderPhase.Review ?: return
        if (current.saving) return
        mutableUi.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            try {
                services.outbox.enqueueAudio(
                    id = captureId,
                    recording = file,
                    mimeType = format.mimeType,
                    durationMs = review.durationMs,
                    title = normalizeTitle(current.title),
                    tags = parseTags(current.tags),
                    entryDate = isoDate(services.today()),
                )
                handle.remove<Long>(KEY_DURATION)
                mutableUi.update { it.copy(saving = false, phase = RecorderPhase.Saved) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                mutableUi.update {
                    it.copy(
                        saving = false,
                        error = UserError.unexpected("Save the recording on this phone", error),
                    )
                }
            }
        }
    }

    /** Deletes the recording (in progress or stopped). */
    fun discard() {
        ticker?.cancel()
        try {
            recorder.cancel()
        } catch (error: VoiceRecorder.RecorderException) {
            fail(error)
        }
        if (file.exists() && !file.delete()) {
            mutableUi.update {
                it.copy(
                    error =
                        UserError(
                            "RECORDING_DISCARD_FAILED",
                            "The recording ${file.name} could not be deleted.",
                            "Clear the app's cache in Settings > Apps > Journal > Storage.",
                            emptyList(),
                        ),
                )
            }
        }
        handle.remove<Long>(KEY_DURATION)
    }

    private fun fail(error: VoiceRecorder.RecorderException) {
        ticker?.cancel()
        mutableUi.update {
            it.copy(
                error =
                    UserError(
                        code = error.code,
                        message = error.message ?: "Recording failed.",
                        hint = "Close other apps that use the microphone, then record again.",
                        details =
                            listOfNotNull(
                                error.cause?.let { cause ->
                                    dev.msj.journal.core.api
                                        .DetailLine("Cause", "${cause.javaClass.name}: ${cause.message}")
                                },
                            ),
                    ),
            )
        }
    }

    private fun removeStaleRecordings() {
        // Leftovers of recordings that were never saved. One that cannot be deleted only costs
        // cache space and is tried again on the next recording, so the result is not needed.
        recordingDir.listFiles()?.filter { it != file }?.forEach { stale -> stale.delete() }
    }

    override fun onCleared() {
        ticker?.cancel()
        if (recorder.isActive) {
            try {
                recorder.cancel()
            } catch (_: VoiceRecorder.RecorderException) {
                // The screen is gone; the partial file is removed with the next recording's cleanup.
            }
        }
        super.onCleared()
    }

    private companion object {
        const val KEY_ID = "capture_id"
        const val KEY_DURATION = "duration_ms"
        const val KEY_TITLE = "title"
        const val KEY_TAGS = "tags"
        const val TICK_MS = 100L
        const val LEVELS = 48
    }
}
