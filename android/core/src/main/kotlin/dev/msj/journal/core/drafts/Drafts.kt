package dev.msj.journal.core.drafts

/**
 * Field logic for the edit-conflict flow (web/src/lib/drafts.ts): when an entry
 * changed on the server while being edited, keep what the user changed and take
 * the server's values for everything else.
 */
public data class DraftFields(
    val title: String,
    val tags: List<String>,
    /** `YYYY-MM-DD`. */
    val date: String,
    val text: String,
)

public enum class DraftField { TITLE, DATE, TAGS, TEXT }

/** The fields the user changed; null means untouched. */
public data class DraftChanges(
    val title: String? = null,
    val tags: List<String>? = null,
    val date: String? = null,
    val text: String? = null,
) {
    public val isEmpty: Boolean get() = title == null && tags == null && date == null && text == null
}

private fun same(
    field: DraftField,
    a: DraftFields,
    b: DraftFields,
): Boolean =
    when (field) {
        DraftField.TAGS -> a.tags == b.tags
        DraftField.TEXT -> a.text.trim() == b.text.trim()
        DraftField.TITLE -> a.title.trim() == b.title.trim()
        DraftField.DATE -> a.date == b.date
    }

/** Fields whose values differ between two snapshots (titles and text compare trimmed). */
public fun differingFields(
    a: DraftFields,
    b: DraftFields,
): List<DraftField> = DraftField.entries.filter { !same(it, a, b) }

/** Only the fields the user changed relative to [base]. */
public fun changedFields(
    base: DraftFields,
    values: DraftFields,
): DraftChanges {
    val differing = differingFields(base, values).toSet()
    return DraftChanges(
        title = if (DraftField.TITLE in differing) values.title else null,
        tags = if (DraftField.TAGS in differing) values.tags else null,
        date = if (DraftField.DATE in differing) values.date else null,
        text = if (DraftField.TEXT in differing) values.text else null,
    )
}

public fun applyChanges(
    current: DraftFields,
    changes: DraftChanges,
): DraftFields =
    DraftFields(
        title = changes.title ?: current.title,
        tags = changes.tags ?: current.tags,
        date = changes.date ?: current.date,
        text = changes.text ?: current.text,
    )

/**
 * After the entry changed elsewhere: keep what the user changed relative to
 * [oldBase], and take [newBase]'s values for every field the user left alone.
 */
public fun reapply(
    oldBase: DraftFields,
    newBase: DraftFields,
    values: DraftFields,
): DraftFields = applyChanges(newBase, changedFields(oldBase, values))
