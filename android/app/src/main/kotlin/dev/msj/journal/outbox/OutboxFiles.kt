package dev.msj.journal.outbox

import kotlinx.coroutines.sync.Mutex
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

/**
 * Recordings waiting for upload, in [directory] (on the device:
 * `noBackupFilesDir/outbox`, private to the app and excluded from backups).
 * A recording is named `<record id>.<extension>`. One instance is shared by
 * the repository and the uploader, so [saveLock] covers both.
 */
class OutboxFiles(
    private val directory: File,
) {
    /**
     * Held while a recording is adopted and its record committed, and while
     * orphans are swept, so the sweep never deletes a recording whose record
     * is about to be inserted.
     */
    internal val saveLock: Mutex = Mutex()

    /** The file a recording of record [id] with [extension] lives in. */
    fun fileFor(
        id: String,
        extension: String,
    ): File {
        require(SAFE_NAME.matches(id)) { "outbox record id '$id' is not a safe file name" }
        require(SAFE_NAME.matches(extension)) { "recording extension '$extension' is not a safe file name" }
        return File(directory, "$id.$extension")
    }

    /**
     * Moves [source] to [fileFor] ([id], [extension]) and returns the new file.
     * A rename is tried first; across file systems it falls back to copying to
     * a temporary file, syncing it to disk, renaming it into place and deleting
     * [source]. On failure [source] is left as it was.
     */
    fun adopt(
        source: File,
        id: String,
        extension: String,
    ): File {
        val target = fileFor(id, extension)
        if (!source.isFile) {
            throw IOException("Save recording failed: ${source.path} does not exist or is not a file.")
        }
        ensureDirectory()
        if (source.canonicalFile == target.canonicalFile) return target
        try {
            Files.move(source.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
        } catch (_: AtomicMoveNotSupportedException) {
            copyDurably(source, target)
            if (!source.delete()) {
                throw IOException(
                    "Save recording: copied ${source.path} to the outbox but could not delete the original.",
                )
            }
        }
        assert(target.isFile) { "adopted recording exists: ${target.path}" }
        return target
    }

    /** Moves [adopted] back to [original] after a save whose record was not committed. */
    fun restore(
        adopted: File,
        original: File,
    ) {
        try {
            Files.move(adopted.toPath(), original.toPath(), StandardCopyOption.REPLACE_EXISTING)
        } catch (error: IOException) {
            throw IOException("Save recording: could not move ${adopted.path} back to ${original.path}.", error)
        }
    }

    /** Lowercase hex SHA-256 of [file], read in a streaming fashion. */
    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(BUFFER_BYTES)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        val bytes = digest.digest()
        val hex = StringBuilder(bytes.size * 2)
        for (byte in bytes) {
            val value = byte.toInt() and 0xff
            hex.append(HEX_DIGITS[value ushr 4]).append(HEX_DIGITS[value and 0x0f])
        }
        return hex.toString()
    }

    /** Deletes the recording at [path] if it is inside the outbox directory; returns whether it is gone. */
    fun delete(path: String): Boolean {
        val file = File(path)
        require(file.canonicalFile.parentFile == directory.canonicalFile) {
            "refusing to delete $path: it is not in the outbox directory ${directory.path}"
        }
        return !file.exists() || file.delete()
    }

    /**
     * Deletes files in the outbox directory that no record references (left by
     * a save cut short before its record was committed). Call it holding
     * [saveLock]. Returns the number deleted.
     */
    fun deleteOrphans(referencedPaths: Set<String>): Int {
        assert(saveLock.isLocked) { "orphans are swept under the save lock" }
        val referenced = referencedPaths.map { File(it).canonicalPath }.toSet()
        return listFiles().filter { it.canonicalPath !in referenced }.count { it.delete() }
    }

    /** Deletes every recording; returns whether the directory is empty afterwards. */
    fun wipe(): Boolean = listFiles().map { it.delete() }.all { it }

    private fun listFiles(): List<File> = directory.listFiles()?.filter { it.isFile }.orEmpty()

    private fun ensureDirectory() {
        if (!directory.isDirectory && !directory.mkdirs() && !directory.isDirectory) {
            throw IOException("Save recording failed: cannot create the outbox directory ${directory.path}.")
        }
    }

    private fun copyDurably(
        source: File,
        target: File,
    ) {
        val temporary = File(directory, "${target.name}.part")
        try {
            source.inputStream().use { input ->
                FileOutputStream(temporary).use { output ->
                    input.copyTo(output, BUFFER_BYTES)
                    output.fd.sync()
                }
            }
            Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
        } catch (error: IOException) {
            if (temporary.exists() && !temporary.delete()) {
                error.addSuppressed(IOException("Could not remove the partial copy ${temporary.path}."))
            }
            throw error
        }
    }

    private companion object {
        const val BUFFER_BYTES = 64 * 1024
        const val HEX_DIGITS = "0123456789abcdef"
        val SAFE_NAME = Regex("^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
    }
}
