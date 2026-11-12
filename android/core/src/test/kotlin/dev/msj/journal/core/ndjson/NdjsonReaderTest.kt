package dev.msj.journal.core.ndjson

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okio.Buffer
import okio.Source
import okio.Timeout
import okio.buffer
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

class NdjsonReaderTest {
    /** Hands out at most [chunk] bytes per read, like a socket delivering small packets. */
    private class Trickle(
        private val data: ByteArray,
        private val chunk: Int,
    ) : Source {
        private var position = 0

        override fun read(
            sink: Buffer,
            byteCount: Long,
        ): Long {
            if (position >= data.size) return -1
            val n = minOf(chunk.toLong(), byteCount, (data.size - position).toLong()).toInt()
            sink.write(data, position, n)
            position += n
            return n.toLong()
        }

        override fun timeout(): Timeout = Timeout.NONE

        override fun close() = Unit
    }

    private fun reader(
        text: String,
        chunk: Int = Int.MAX_VALUE,
    ) = NdjsonReader(Trickle(text.toByteArray(Charsets.UTF_8), chunk).buffer())

    private fun NdjsonReader.all(): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        while (true) out += (next() ?: return out) as JsonObject
    }

    @Test
    fun `yields one value per line including an unterminated last line`() {
        val values = reader("{\"a\":1}\n{\"a\":2}\n{\"a\":3}").all()
        assertEquals(listOf(1, 2, 3), values.map { (it["a"] as JsonPrimitive).content.toInt() })
    }

    @Test
    fun `handles CRLF and skips blank lines while counting them`() {
        val r = reader("{\"a\":1}\r\n\r\n   \r\n{\"a\":2}\r\n")
        r.next()
        assertEquals(1, r.lineNumber)
        r.next()
        assertEquals(4, r.lineNumber)
        assertNull(r.next())
    }

    @Test
    fun `multi-byte characters split across reads survive`() {
        val text = "{\"s\":\"café 日本語 😀\"}\n"
        for (chunk in 1..5) {
            val value = reader(text, chunk).all().single()
            assertEquals("café 日本語 😀", (value["s"] as JsonPrimitive).content, "chunk=$chunk")
        }
    }

    @Test
    fun `a line is yielded as soon as it is complete`() {
        val buffer = Buffer()
        val r = NdjsonReader(buffer)
        buffer.writeUtf8("{\"a\":1}\n{\"a\":")
        assertEquals(1, ((r.next() as JsonObject)["a"] as JsonPrimitive).content.toInt())
        // The second line is incomplete, so the source is read to exhaustion and the partial line is the final line.
        assertFailsWith<NdjsonLineError> { r.next() }
    }

    @Test
    fun `invalid JSON reports the line number and a bounded text`() {
        val long = "{\"k\":" + "x".repeat(500)
        val r = reader("{\"a\":1}\n\n$long\n")
        r.next()
        val error = assertFailsWith<NdjsonLineError> { r.next() }
        assertEquals(3, error.line)
        assertTrue(error.text.length <= 123, "text is bounded: ${error.text.length}")
        assertIs<Throwable>(error.cause)
        assertTrue(error.message.orEmpty().contains("line 3"))
    }

    @Test
    fun `empty input yields nothing`() {
        assertNull(reader("").next())
        assertNull(reader("\n\n").next())
    }
}
