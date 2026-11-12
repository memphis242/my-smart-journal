package dev.msj.journal.session

import dev.msj.journal.core.api.JournalApi
import dev.msj.journal.ui.UserError
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Who the app is signed in as: the server address and an API client holding
 * the token. Restoring needs no network, so the app opens signed in and
 * offline; a token the server later rejects is reported where it is used,
 * and signing in again replaces it without touching unsent entries.
 */
class SessionManager(
    private val prefs: AppPrefs,
    private val vault: TokenVault,
    private val factory: ConnectionFactory,
    scope: CoroutineScope,
) : ApiSource {
    sealed interface State {
        data object Loading : State

        data class SignedOut(
            /** The last server address, to prefill the form. */
            val lastServer: String?,
            /** Why the app is signed out when it was not the user's choice. */
            val problem: UserError?,
        ) : State

        data class SignedIn(
            val server: String,
            val host: String,
            val api: JournalApi,
        ) : State {
            override fun toString(): String = "SignedIn(server=$server)"
        }
    }

    private val mutableState = MutableStateFlow<State>(State.Loading)
    val state: StateFlow<State> = mutableState.asStateFlow()
    private val restored = CompletableDeferred<Unit>()

    init {
        scope.launch(Dispatchers.IO) {
            mutableState.value = restore()
            restored.complete(Unit)
        }
    }

    override suspend fun current(): JournalApi? {
        restored.await()
        return (state.value as? State.SignedIn)?.api
    }

    /** Stores the token (encrypted) and the server; the caller has already verified both against the server. */
    suspend fun signIn(
        server: ParsedServer.Valid,
        token: String,
        serverIsPrivate: Boolean,
    ) {
        assert(token.isNotBlank()) { "a verified token is not blank" }
        withContext(Dispatchers.IO) {
            restored.await()
            vault.store(token)
            prefs.serverBase = server.base
            prefs.serverIsPrivate = serverIsPrivate
            mutableState.value = State.SignedIn(server.base, server.host, factory.create(server.base, token))
        }
    }

    /** Forgets the token and the server. The caller wipes the outbox first. */
    suspend fun signOut() {
        withContext(Dispatchers.IO) {
            restored.await()
            vault.wipe()
            prefs.clearSession()
            mutableState.value = State.SignedOut(lastServer = null, problem = null)
        }
    }

    private fun restore(): State {
        val base = prefs.serverBase ?: return State.SignedOut(lastServer = null, problem = null)
        return when (val loaded = vault.load()) {
            TokenVault.Loaded.Absent -> State.SignedOut(lastServer = base, problem = null)
            is TokenVault.Loaded.Unreadable ->
                State.SignedOut(
                    lastServer = base,
                    problem =
                        UserError(
                            code = "TOKEN_UNREADABLE",
                            message = "The saved access token could not be read. ${loaded.reason}",
                            hint = "Sign in again with the token printed by `msj create-token` on the server.",
                            details = emptyList(),
                        ),
                )
            is TokenVault.Loaded.Present ->
                when (val parsed = factory.parse(base)) {
                    is ParsedServer.Valid -> State.SignedIn(parsed.base, parsed.host, factory.create(parsed.base, loaded.token))
                    is ParsedServer.Invalid -> State.SignedOut(lastServer = base, problem = UserError.of(parsed.error))
                }
        }
    }
}
