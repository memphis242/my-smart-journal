package dev.msj.journal.ui.components

import androidx.compose.ui.semantics.SemanticsPropertyKey
import androidx.compose.ui.semantics.SemanticsPropertyReceiver

/**
 * Stable hooks for instrumented tests. Tests find nodes by these tags and read
 * data through the semantics keys below, never by visible text or layout.
 */
object TestTags {
    const val THEME_SWITCH = "theme.switch"

    const val SIGN_IN_SCREEN = "signin.screen"
    const val SIGN_IN_URL = "signin.url"
    const val SIGN_IN_TOKEN = "signin.token"
    const val SIGN_IN_TEST = "signin.test"
    const val SIGN_IN_SUBMIT = "signin.submit"
    const val SIGN_IN_ALLOW_LOCAL_NETWORK = "signin.allow-local-network"
    const val SIGN_IN_STEP_PREFIX = "signin.step."

    const val HOME_SCREEN = "home.screen"
    const val RANGE_PREFIX = "range."
    const val ENTRY_LIST = "entries.list"
    const val ENTRY_ROW_PREFIX = "entry.row."
    const val ENTRIES_EMPTY = "entries.empty"
    const val ENTRIES_ERROR = "entries.error"

    const val QUEUE_STRIP = "queue.strip"
    const val QUEUE_STALE = "queue.stale"
    const val QUEUE_RUN_NOW = "queue.run-now"
    const val QUEUE_ERROR = "queue.error"

    const val UNSENT_STRIP = "unsent.strip"
    const val CAPTURE_RECORD = "capture.record"
    const val CAPTURE_WRITE = "capture.write"

    const val COMPOSER_SCREEN = "composer.screen"
    const val COMPOSER_TEXT = "composer.text"
    const val COMPOSER_TITLE = "composer.title"
    const val COMPOSER_TAGS = "composer.tags"
    const val COMPOSER_SAVE = "composer.save"
    const val COMPOSER_CLOSE = "composer.close"

    const val RECORDER_SCREEN = "recorder.screen"
    const val RECORDER_START = "recorder.start"
    const val RECORDER_STOP = "recorder.stop"
    const val RECORDER_SAVE = "recorder.save"
    const val RECORDER_DISCARD = "recorder.discard"
    const val RECORDER_ERROR = "recorder.error"

    const val DETAIL_SCREEN = "detail.screen"
    const val DETAIL_OLDER = "detail.older"
    const val DETAIL_NEWER = "detail.newer"
    const val DETAIL_COPY = "detail.copy"
    const val DETAIL_EDIT = "detail.edit"
    const val DETAIL_SUMMARY = "detail.summary"
    const val DETAIL_TEXT = "detail.text"
    const val DETAIL_RAW = "detail.raw"
    const val DETAIL_RAW_TOGGLE = "detail.raw.toggle"
    const val DETAIL_ERROR = "detail.error"

    const val EDITOR_SCREEN = "editor.screen"
    const val EDITOR_TEXT = "editor.text"
    const val EDITOR_TITLE = "editor.title"
    const val EDITOR_SAVE = "editor.save"
    const val EDITOR_CONFLICT = "editor.conflict"
    const val EDITOR_REAPPLY = "editor.reapply"

    const val OUTBOX_SCREEN = "outbox.screen"
    const val OUTBOX_ROW_PREFIX = "outbox.row."
    const val OUTBOX_RETRY_PREFIX = "outbox.retry."
    const val OUTBOX_DISCARD_PREFIX = "outbox.discard."
    const val OUTBOX_EMPTY = "outbox.empty"

    const val SEARCH_OPEN = "search.open"
    const val SEARCH_SCREEN = "search.screen"
    const val SEARCH_FIELD = "search.field"
    const val SEARCH_RESULT_PREFIX = "search.result."
    const val SEARCH_DONE = "search.done"
    const val SEARCH_ERROR = "search.error"

    const val MENU = "menu"
    const val MENU_OUTBOX = "menu.outbox"
    const val MENU_SIGN_OUT = "menu.sign-out"
    const val MENU_SETTINGS = "menu.settings"
    const val CONFIRM_ACTION = "confirm.action"
    const val CONFIRM_CANCEL = "confirm.cancel"

    const val ERROR_CODE = "error.code"
    const val ERROR_DETAILS_TOGGLE = "error.details.toggle"
    const val SNACKBAR = "snackbar"
}

/** The entry a row or detail view shows. */
val EntryIdKey = SemanticsPropertyKey<String>("EntryId")
var SemanticsPropertyReceiver.entryId by EntryIdKey

/** A count a status display shows (queue counts, unsent uploads). */
val CountKey = SemanticsPropertyKey<Int>("Count")
var SemanticsPropertyReceiver.count by CountKey

/** A machine-readable state name (outbox state, queue state, sign-in step state). */
val StateKey = SemanticsPropertyKey<String>("State")
var SemanticsPropertyReceiver.stateName by StateKey

/** The error code a notice shows. */
val ErrorCodeKey = SemanticsPropertyKey<String>("ErrorCode")
var SemanticsPropertyReceiver.errorCode by ErrorCodeKey
