package dev.msj.journal.ui.nav

import kotlinx.serialization.Serializable

@Serializable
data class SignInRoute(
    /** True when signing in again to replace a rejected token; unsent entries stay. */
    val again: Boolean = false,
)

@Serializable
data object HomeRoute

@Serializable
data class EntryRoute(
    val id: String,
)

@Serializable
data class EditorRoute(
    val id: String,
)

@Serializable
data class ComposerRoute(
    /** Id of text shared from another app (see SharedTextStore); the text itself is too large for a route. */
    val sharedId: String? = null,
)

@Serializable
data object RecorderRoute

@Serializable
data object OutboxRoute

@Serializable
data object SearchRoute

@Serializable
data object SettingsRoute

@Serializable
data object LicensesRoute
