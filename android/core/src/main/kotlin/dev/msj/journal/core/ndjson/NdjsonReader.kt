package dev.msj.journal.core.ndjson

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import okio.BufferedSource
import java.io.IOException

/** A line that is not valid JSON; [text] is the line, shortened to 120 characters. */
public class NdjsonLineError(
    public val line: Int,
    text: String,
    cause: Throwable?,
) : Exception("line $line is not valid JSON: ${shorten(text)}", cause) {
    public val text: String = shorten(text)

    private companion object {
        const val SHOWN = 120

        fun shorten(text: String): String = if (text.length > SHOWN) text.take(SHOWN) + "..." else text
    }
}

/**
 * Incremental reader for newline-delimited JSON: [next] blocks until one line
 * is complete and returns it parsed, or null at the end of the stream. Lines
 * end with `\n` or `\r\n`; the last line may lack a terminator; blank lines
 * are skipped. Multi-byte characters split across network reads are reassembled
 * because a line is decoded only once it is complete.
 *
 * Not thread-safe; [source] is not closed by the reader.
 */
public class NdjsonReader(
    private val source: BufferedSource,
) {
    private var physicalLines = 0

    /** The 1-based line number of the value [next] returned last (or of the line that failed). */
    public val lineNumber: Int get() = physicalLines

    /** @throws NdjsonLineError when a line is not valid JSON. @throws IOException when the source fails. */
    @Throws(IOException::class, NdjsonLineError::class)
    public fun next(): JsonElement? {
        while (true) {
            val raw = source.readUtf8Line() ?: return null
            physicalLines += 1
            val text = raw.trim()
            if (text.isEmpty()) continue
            return try {
                Json.parseToJsonElement(text)
            } catch (e: SerializationException) {
                throw NdjsonLineError(physicalLines, text, e)
            } catch (e: IllegalArgumentException) {
                throw NdjsonLineError(physicalLines, text, e)
            }
        }
    }
}
