package dev.msj.journal.core.api

import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class ServerUrlTest {
    private val cleartext = setOf("10.0.2.2", "127.0.0.1")

    private fun valid(input: String): ServerUrl =
        assertIs<ServerUrlResult.Valid>(ServerUrl.parse(input, cleartext), input).url

    private fun invalid(input: String): ApiError =
        assertIs<ServerUrlResult.Invalid>(ServerUrl.parse(input, cleartext), input).error

    @Test
    fun `scheme-less input becomes https and is normalized`() {
        assertEquals("https://journal.example.com", valid("  journal.example.com/ \n").toString())
        assertEquals("https://journal.example.com:8443", valid("journal.example.com:8443").toString())
    }

    @Test
    fun `trailing slash and a pasted api prefix are stripped`() {
        assertEquals("https://journal.example.com", valid("https://journal.example.com/api/v1").toString())
        assertEquals("https://journal.example.com", valid("https://journal.example.com/api/v1/").toString())
        assertEquals(
            "https://journal.example.com/journal",
            valid("https://journal.example.com/journal/api/v1/").toString(),
        )
    }

    @Test
    fun `api joins under the base path`() {
        assertEquals("https://h.example/api/v1/entries/x", valid("https://h.example").api("/entries/x").toString())
        assertEquals("https://h.example/m/api/v1/entries", valid("https://h.example/m/").api("/entries").toString())
        assertEquals(
            "https://h.example/api/v1/entries/a%2Fb",
            valid("https://h.example").api("/entries/a%2Fb").toString(),
        )
    }

    @Test
    fun `explicit ports are kept and the host is exposed`() {
        val url = valid("https://journal.example.com:8443")
        assertEquals("journal.example.com", url.host)
        assertEquals("https://journal.example.com:8443/api/v1/health", url.api("/health").toString())
    }

    @Test
    fun `http is allowed only for cleartext hosts`() {
        assertEquals("http://10.0.2.2:8000", valid("http://10.0.2.2:8000").toString())
        assertEquals("http://127.0.0.1:8000", valid("http://127.0.0.1:8000/").toString())
        val error = invalid("http://journal.example.com")
        assertEquals(ClientCodes.INVALID_SERVER_URL, error.code)
        assertTrue(error.message.contains("http://"), error.message)
        assertTrue(error.hint.orEmpty().contains("https://journal.example.com"), error.hint)
    }

    @Test
    fun `rejections name the problem and the fix`() {
        val cases =
            mapOf(
                "" to "empty",
                "ftp://journal.example.com" to "ftp://",
                "https://user:pw@journal.example.com" to "user name or password",
                "https://journal.example.com/?a=1" to "query",
                "https://journal.example.com/#top" to "fragment",
                "https:///path" to "no host name",
                "https://exa mple.com" to "valid web address",
            )
        for ((input, expected) in cases) {
            val error = invalid(input)
            assertEquals(ClientCodes.INVALID_SERVER_URL, error.code, input)
            assertEquals(ErrorKind.NETWORK, error.kind, input)
            assertEquals(Delivery.NOT_SENT, error.delivery, input)
            assertTrue(error.message.contains(expected), "'$input' -> ${error.message}")
            assertTrue(error.hint.orEmpty().contains("example"), "'$input' -> ${error.hint}")
        }
    }
}
