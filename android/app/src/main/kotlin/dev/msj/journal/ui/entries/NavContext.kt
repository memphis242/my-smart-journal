package dev.msj.journal.ui.entries

import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.EntrySummary

/**
 * The ordered list a detail view was opened from, so it can step to the next
 * newer and the next older entry (web/src/state/entry-modal.ts). Past the
 * loaded rows of an incomplete list, `GET /entries/{id}/neighbors` fills in.
 */
data class NavContext(
    /** Ids ordered newest first. */
    val newestFirst: List<String>,
    /** Summaries for the ids above, for instant rendering while details load. */
    val previews: Map<String, EntrySummary>,
    /** The list's filters, for the neighbors request past the loaded rows. */
    val filters: EntryFilters,
    /** False when the server has more matching entries than are loaded. */
    val complete: Boolean,
) {
    data class Local(
        val newer: String?,
        val older: String?,
        val index: Int,
    )

    fun local(id: String): Local {
        val index = newestFirst.indexOf(id)
        if (index < 0) return Local(newer = null, older = null, index = -1)
        return Local(
            newer = if (index > 0) newestFirst[index - 1] else null,
            older = newestFirst.getOrNull(index + 1),
            index = index,
        )
    }

    /** True when the server must be asked for a neighbor the loaded rows do not have. */
    fun needsServer(id: String): Boolean {
        val local = local(id)
        return !complete && (local.index < 0 || local.newer == null || local.older == null)
    }

    companion object {
        /** A list shown newest first. */
        fun fromList(
            entries: List<EntrySummary>,
            filters: EntryFilters,
            complete: Boolean,
        ): NavContext =
            NavContext(
                newestFirst = entries.map { it.id },
                previews = entries.associateBy { it.id },
                filters = filters,
                complete = complete,
            )

        /** Search results arrive ranked; stepping goes by date like the list (date, then created, then id). */
        fun chronological(entries: List<EntrySummary>): NavContext {
            val sorted =
                entries.sortedWith(
                    compareByDescending<EntrySummary> { it.entryDate }
                        .thenByDescending { it.createdAt }
                        .thenByDescending { it.id },
                )
            return NavContext(
                newestFirst = sorted.map { it.id },
                previews = entries.associateBy { it.id },
                filters = EntryFilters.NONE,
                complete = true,
            )
        }

        /** Opened on its own (for example from the outbox): nothing to step through except via the server. */
        fun single(id: String): NavContext = NavContext(listOf(id), emptyMap(), EntryFilters.NONE, complete = false)
    }
}
