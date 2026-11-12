package dev.msj.journal.ui.capture

/** Tags typed as a comma-separated list: trimmed, blanks dropped, duplicates (ignoring case) removed, first spelling kept. */
fun parseTags(input: String): List<String> {
    val seen = HashSet<String>()
    return input
        .split(',')
        .map { it.trim() }
        .filter { it.isNotEmpty() && seen.add(it.lowercase()) }
}

/** A title the user left blank is no title. */
fun normalizeTitle(input: String): String? = input.trim().ifEmpty { null }
