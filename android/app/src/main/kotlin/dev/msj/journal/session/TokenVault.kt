package dev.msj.journal.session

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.security.GeneralSecurityException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Keeps the access token encrypted at rest with an AES-256-GCM key that lives
 * in the Android Keystore (StrongBox when the device has it) and never leaves
 * it. The ciphertext sits in no-backup storage, so a restored backup cannot
 * carry it to another device; a key that the Keystore lost (device reset,
 * restore) makes the stored token unreadable, which is reported, not hidden.
 *
 * The token is never logged; failures name the operation and the cause class
 * only.
 */
class TokenVault(
    context: Context,
    private val alias: String = KEY_ALIAS,
) {
    private val file: File = File(context.noBackupFilesDir, FILE_NAME)

    sealed interface Loaded {
        data object Absent : Loaded

        data class Present(
            val token: String,
        ) : Loaded {
            override fun toString(): String = "Present(token=<redacted>)"
        }

        /** A stored token exists but cannot be decrypted; signing in again replaces it. */
        data class Unreadable(
            val reason: String,
        ) : Loaded
    }

    fun store(token: String) {
        assert(token.isNotBlank()) { "the token to store is not blank" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        try {
            cipher.init(Cipher.ENCRYPT_MODE, keyForEncryption())
        } catch (_: GeneralSecurityException) {
            // A key the Keystore can no longer use (for example invalidated): replace it once.
            deleteKey()
            try {
                cipher.init(Cipher.ENCRYPT_MODE, keyForEncryption())
            } catch (again: GeneralSecurityException) {
                throw TokenVaultException("Encrypt the access token with a new Keystore key", again)
            }
        }
        val iv = cipher.iv
        assert(iv.size == GCM_IV_BYTES) { "GCM IV is $GCM_IV_BYTES bytes" }
        val sealed = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val payload =
            ByteBuffer
                .allocate(1 + 1 + iv.size + sealed.size)
                .put(FORMAT_VERSION)
                .put(iv.size.toByte())
                .put(iv)
                .put(sealed)
                .array()
        writeAtomically(payload)
    }

    fun load(): Loaded {
        if (!file.isFile) return Loaded.Absent
        val payload =
            try {
                file.readBytes()
            } catch (error: IOException) {
                return Loaded.Unreadable("The stored token file could not be read (${error.javaClass.simpleName}).")
            }
        if (payload.size < 2 + GCM_IV_BYTES + GCM_TAG_BITS / 8 || payload[0] != FORMAT_VERSION) {
            return Loaded.Unreadable("The stored token has an unknown format (version ${payload.firstOrNull()}).")
        }
        val ivSize = payload[1].toInt()
        if (ivSize != GCM_IV_BYTES) return Loaded.Unreadable("The stored token has an invalid header.")
        val key = existingKey() ?: return Loaded.Unreadable("The phone no longer has the key that protected the token.")
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, payload, 2, ivSize))
            val clear = cipher.doFinal(payload, 2 + ivSize, payload.size - 2 - ivSize)
            Loaded.Present(String(clear, Charsets.UTF_8))
        } catch (error: GeneralSecurityException) {
            Loaded.Unreadable("The stored token could not be decrypted (${error.javaClass.simpleName}).")
        }
    }

    /** Deletes the stored token and its key. */
    fun wipe() {
        if (file.exists() && !file.delete()) {
            throw TokenVaultException(
                "Delete the stored access token",
                IOException("delete returned false for ${file.name}"),
            )
        }
        deleteKey()
    }

    private fun writeAtomically(payload: ByteArray) {
        val temporary = File(file.parentFile, "$FILE_NAME.tmp")
        try {
            temporary.outputStream().use { stream ->
                stream.write(payload)
                stream.fd.sync()
            }
            if (!temporary.renameTo(file)) throw IOException("rename to ${file.name} returned false")
        } catch (error: IOException) {
            val removed = temporary.delete()
            throw TokenVaultException("Save the access token (temporary file removed: $removed)", error)
        }
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

    private fun existingKey(): SecretKey? = (keyStore().getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.secretKey

    private fun deleteKey() {
        val store = keyStore()
        if (store.containsAlias(alias)) store.deleteEntry(alias)
    }

    private fun keyForEncryption(): SecretKey = existingKey() ?: generateKey()

    private fun generateKey(): SecretKey {
        fun spec(strongBox: Boolean): KeyGenParameterSpec =
            KeyGenParameterSpec
                .Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .setRandomizedEncryptionRequired(true)
                .apply { if (strongBox) setIsStrongBoxBacked(true) }
                .build()
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        return try {
            generator.init(spec(strongBox = true))
            generator.generateKey()
        } catch (_: StrongBoxUnavailableException) {
            // No StrongBox on this device (emulators, many phones): the TEE-backed Keystore protects the key instead.
            generator.init(spec(strongBox = false))
            generator.generateKey()
        }
    }

    companion object {
        const val KEY_ALIAS: String = "msj_access_token_v1"
        private const val FILE_NAME = "access-token.v1"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val KEY_BITS = 256
        private const val GCM_IV_BYTES = 12
        private const val GCM_TAG_BITS = 128
        private const val FORMAT_VERSION: Byte = 1
    }
}

class TokenVaultException(
    operation: String,
    cause: Throwable,
) : Exception("$operation failed: ${cause.javaClass.simpleName}", cause)
