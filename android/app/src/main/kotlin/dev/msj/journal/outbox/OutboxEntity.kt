package dev.msj.journal.outbox

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import dev.msj.journal.core.api.Delivery
import dev.msj.journal.core.api.DetailLine
import dev.msj.journal.core.outbox.OutboxError
import dev.msj.journal.core.outbox.OutboxPayload
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/**
 * One [OutboxRecord] as a flat row. Enums are stored by name, lists as JSON.
 * The payload columns of the other kind are null: `text` for TEXT, the
 * `audio*` columns for AUDIO; the error columns are all null or all set.
 */
@Entity(
    tableName = "outbox",
    indices = [Index(value = ["state", "nextAttemptAtMs"]), Index(value = ["createdAtMs"])],
)
internal data class OutboxEntity(
    @PrimaryKey val id: String,
    val createdAtMs: Long,
    val entryDate: String,
    val title: String?,
    val tagsJson: String,
    val payloadKind: String,
    val text: String?,
    val audioPath: String?,
    val audioMimeType: String?,
    val audioFileName: String?,
    val audioSha256: String?,
    val audioSizeBytes: Long?,
    val audioDurationMs: Long?,
    val state: String,
    val attempts: Int,
    val nextAttemptAtMs: Long,
    val firstSentAtMs: Long?,
    val errorCode: String?,
    val errorMessage: String?,
    val errorHint: String?,
    val errorDetailsJson: String?,
    val errorStatus: Int?,
    val errorDelivery: String?,
    val errorAtMs: Long?,
    val serverEntryId: String?,
    val updatedAtMs: Long,
)

internal object PayloadKind {
    const val TEXT: String = "TEXT"
    const val AUDIO: String = "AUDIO"
}

@Serializable
private data class StoredDetailLine(
    val label: String,
    val value: String,
)

private val json = Json { ignoreUnknownKeys = false }
private val tagsSerializer = ListSerializer(String.serializer())
private val detailsSerializer = ListSerializer(StoredDetailLine.serializer())

internal fun OutboxRecord.toEntity(): OutboxEntity {
    val payload = payload
    val text = (payload as? OutboxPayload.Text)?.text
    val audio = payload as? OutboxPayload.Audio
    val error = lastError
    return OutboxEntity(
        id = id,
        createdAtMs = createdAtMs,
        entryDate = entryDate,
        title = title,
        tagsJson = json.encodeToString(tagsSerializer, tags),
        payloadKind =
            when (payload) {
                is OutboxPayload.Text -> PayloadKind.TEXT
                is OutboxPayload.Audio -> PayloadKind.AUDIO
            },
        text = text,
        audioPath = audio?.path,
        audioMimeType = audio?.mimeType,
        audioFileName = audio?.fileName,
        audioSha256 = audio?.sha256,
        audioSizeBytes = audio?.sizeBytes,
        audioDurationMs = audio?.durationMs,
        state = state.name,
        attempts = attempts,
        nextAttemptAtMs = nextAttemptAtMs,
        firstSentAtMs = firstSentAtMs,
        errorCode = error?.code,
        errorMessage = error?.message,
        errorHint = error?.hint,
        errorDetailsJson =
            error?.let { e ->
                json.encodeToString(detailsSerializer, e.details.map { StoredDetailLine(it.label, it.value) })
            },
        errorStatus = error?.status,
        errorDelivery = error?.delivery?.name,
        errorAtMs = error?.atMs,
        serverEntryId = serverEntryId,
        updatedAtMs = updatedAtMs,
    )
}

/**
 * The record this row stores. A row this app did not write consistently (an
 * unknown state name, a missing payload column) is a corrupt database, so it
 * fails with [IllegalStateException] naming the record and the column.
 */
internal fun OutboxEntity.toRecord(): OutboxRecord =
    OutboxRecord(
        id = id,
        createdAtMs = createdAtMs,
        entryDate = entryDate,
        title = title,
        tags = json.decodeFromString(tagsSerializer, tagsJson),
        payload = payload(),
        state = enumColumn<OutboxState>("state", state),
        attempts = attempts,
        nextAttemptAtMs = nextAttemptAtMs,
        firstSentAtMs = firstSentAtMs,
        lastError = storedError(),
        serverEntryId = serverEntryId,
        updatedAtMs = updatedAtMs,
    )

private fun OutboxEntity.payload(): OutboxPayload =
    when (payloadKind) {
        PayloadKind.TEXT -> {
            OutboxPayload.Text(text = column("text", text))
        }

        PayloadKind.AUDIO -> {
            OutboxPayload.Audio(
                path = column("audioPath", audioPath),
                mimeType = column("audioMimeType", audioMimeType),
                fileName = column("audioFileName", audioFileName),
                sha256 = column("audioSha256", audioSha256),
                sizeBytes = column("audioSizeBytes", audioSizeBytes),
                durationMs = column("audioDurationMs", audioDurationMs),
            )
        }

        else -> {
            error("Outbox record $id has an unknown payloadKind '$payloadKind'")
        }
    }

private fun OutboxEntity.storedError(): OutboxError? {
    val code = errorCode ?: return null
    return OutboxError(
        code = code,
        message = column("errorMessage", errorMessage),
        hint = errorHint,
        details =
            json
                .decodeFromString(detailsSerializer, column("errorDetailsJson", errorDetailsJson))
                .map { DetailLine(it.label, it.value) },
        status = errorStatus,
        delivery = enumColumn<Delivery>("errorDelivery", column("errorDelivery", errorDelivery)),
        atMs = column("errorAtMs", errorAtMs),
    )
}

private fun <T : Any> OutboxEntity.column(
    name: String,
    value: T?,
): T = checkNotNull(value) { "Outbox record $id (payloadKind $payloadKind) has no $name" }

private inline fun <reified E : Enum<E>> OutboxEntity.enumColumn(
    name: String,
    value: String,
): E =
    enumValues<E>().firstOrNull { it.name == value }
        ?: error("Outbox record $id has an unknown $name '$value'")
