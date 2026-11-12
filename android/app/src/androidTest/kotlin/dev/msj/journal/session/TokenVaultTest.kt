package dev.msj.journal.session

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.KeyStore
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

/** The Keystore-backed token store on a real device: round trip, wipe, and a lost key reported as unreadable. */
@RunWith(AndroidJUnit4::class)
class TokenVaultTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val alias = "msj_test_token_key"
    private val vault = TokenVault(context, alias)

    @After
    fun cleanUp() = vault.wipe()

    @Test
    fun storesEncryptedAndReadsBack() {
        val token = "tok_" + "x".repeat(40)
        vault.store(token)
        assertEquals(TokenVault.Loaded.Present(token), vault.load())
        val stored = File(context.noBackupFilesDir, "access-token.v1").readBytes()
        assertFalse(String(stored, Charsets.ISO_8859_1).contains(token), "the file holds ciphertext, not the token")
        assertTrue(!vault.load().toString().contains(token), "toString never shows the token")
    }

    @Test
    fun wipeRemovesTokenAndKey() {
        vault.store("tok_wipe")
        vault.wipe()
        assertEquals(TokenVault.Loaded.Absent, vault.load())
        assertFalse(KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.containsAlias(alias))
    }

    @Test
    fun lostKeyMakesTheTokenUnreadableInsteadOfFailingSilently() {
        vault.store("tok_lost")
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
        assertIs<TokenVault.Loaded.Unreadable>(vault.load())
        vault.store("tok_new")
        assertEquals(TokenVault.Loaded.Present("tok_new"), vault.load())
    }
}
