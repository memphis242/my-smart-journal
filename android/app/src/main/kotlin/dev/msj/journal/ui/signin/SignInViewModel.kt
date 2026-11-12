package dev.msj.journal.ui.signin

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.session.LocalNetworkAccess
import dev.msj.journal.session.ParsedServer
import dev.msj.journal.session.SessionManager
import dev.msj.journal.session.TokenVaultException
import dev.msj.journal.ui.UserError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class CheckStep { ADDRESS, LOCAL_NETWORK, SERVER, TOKEN }

enum class StepState { WAITING, RUNNING, PASSED, FAILED, SKIPPED }

data class SignInUi(
    val url: String = "",
    val token: String = "",
    val steps: Map<CheckStep, StepState> = CheckStep.entries.associateWith { StepState.WAITING },
    val error: UserError? = null,
    val busy: Boolean = false,
    /** The server address is private and Android 17 needs the local-network permission to reach it. */
    val needsLocalNetwork: Boolean = false,
    /** Every check passed for the current inputs (Test connection). */
    val verified: Boolean = false,
    /** Signed in; the screen navigates away. */
    val done: Boolean = false,
) {
    override fun toString(): String = "SignInUi(url=$url, steps=$steps, busy=$busy, done=$done)"
}

/**
 * Checks the server address and token against the real server before
 * anything is stored: Test connection runs the checks, Sign in runs them and
 * then saves. Each distinct failure keeps its own code (unreachable host, TLS,
 * wrong server, rejected token, missing local-network permission).
 */
class SignInViewModel(
    private val services: AppServices,
    private val localNetworkGranted: () -> Boolean,
) : ViewModel() {
    private val mutableUi = MutableStateFlow(SignInUi())
    val ui: StateFlow<SignInUi> = mutableUi.asStateFlow()
    private var running: Job? = null
    private var resumeAfterPermission: Boolean? = null

    init {
        val session = services.session.state.value
        val prefill =
            when (session) {
                is SessionManager.State.SignedIn -> session.server
                is SessionManager.State.SignedOut -> session.lastServer
                SessionManager.State.Loading -> services.prefs.serverBase
            }
        val problem = (session as? SessionManager.State.SignedOut)?.problem
        mutableUi.value = SignInUi(url = prefill.orEmpty(), error = problem)
    }

    fun onUrlChange(value: String) = edit { it.copy(url = value) }

    fun onTokenChange(value: String) = edit { it.copy(token = value) }

    /** Runs every check without saving anything. */
    fun testConnection() = start(save = false)

    /** Runs every check, then stores the token and the server. */
    fun signIn() = start(save = true)

    /** The answer to the local-network permission request the screen launched. */
    fun onLocalNetworkResult(granted: Boolean) {
        val save = resumeAfterPermission ?: return
        resumeAfterPermission = null
        if (granted) {
            mutableUi.update { it.copy(needsLocalNetwork = false) }
            start(save, permissionJustGranted = true)
        } else {
            mutableUi.update {
                it.copy(
                    busy = false,
                    needsLocalNetwork = true,
                    steps = it.steps + (CheckStep.LOCAL_NETWORK to StepState.FAILED),
                    error = localNetworkDenied(),
                )
            }
        }
    }

    private fun edit(change: (SignInUi) -> SignInUi) {
        running?.cancel()
        resumeAfterPermission = null
        // Changed inputs invalidate an earlier result.
        mutableUi.update {
            change(it).copy(
                steps = CheckStep.entries.associateWith { StepState.WAITING },
                verified = false,
                busy = false,
                needsLocalNetwork = false,
                error = null,
            )
        }
    }

    private fun start(
        save: Boolean,
        permissionJustGranted: Boolean = false,
    ) {
        if (mutableUi.value.busy && !permissionJustGranted) return
        running?.cancel()
        running =
            viewModelScope.launch {
                try {
                    run(save, permissionJustGranted)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: TokenVaultException) {
                    fail(null, UserError.unexpected("Save the access token", error).copy(code = "TOKEN_STORE_FAILED"))
                } catch (error: Exception) {
                    fail(null, UserError.unexpected("Sign in", error))
                }
            }
    }

    private fun setStep(
        step: CheckStep,
        state: StepState,
    ) = mutableUi.update { it.copy(steps = it.steps + (step to state)) }

    private fun fail(
        step: CheckStep?,
        error: UserError,
    ) = mutableUi.update {
        it.copy(
            busy = false,
            error = error,
            steps =
                if (step ==
                    null
                ) {
                    it.steps
                } else {
                    it.steps + (step to StepState.FAILED)
                },
        )
    }

    private suspend fun run(
        save: Boolean,
        permissionJustGranted: Boolean,
    ) {
        val input = mutableUi.value
        mutableUi.update {
            it.copy(
                busy = true,
                error = null,
                verified = false,
                steps =
                    if (permissionJustGranted) {
                        it.steps + (CheckStep.LOCAL_NETWORK to StepState.PASSED)
                    } else {
                        CheckStep.entries.associateWith { StepState.WAITING }
                    },
            )
        }

        setStep(CheckStep.ADDRESS, StepState.RUNNING)
        val server =
            when (val parsed = services.connections.parse(input.url)) {
                is ParsedServer.Invalid -> return fail(CheckStep.ADDRESS, UserError.of(parsed.error))
                is ParsedServer.Valid -> parsed
            }
        setStep(CheckStep.ADDRESS, StepState.PASSED)

        val serverIsPrivate: Boolean
        if (!permissionJustGranted) {
            setStep(CheckStep.LOCAL_NETWORK, StepState.RUNNING)
            serverIsPrivate = LocalNetworkAccess.applies && LocalNetworkAccess.hostIsPrivate(server.host)
            when {
                !serverIsPrivate -> {
                    setStep(CheckStep.LOCAL_NETWORK, StepState.SKIPPED)
                }

                localNetworkGranted() -> {
                    setStep(CheckStep.LOCAL_NETWORK, StepState.PASSED)
                }

                else -> {
                    resumeAfterPermission = save
                    mutableUi.update { it.copy(needsLocalNetwork = true) }
                    return
                }
            }
        } else {
            serverIsPrivate = true
        }

        val token = input.token.trim()
        // The health check needs no token; a random placeholder keeps error scrubbing from matching real text.
        val api =
            services.connections.create(
                server.base,
                token.ifEmpty { "placeholder-" + java.util.UUID.randomUUID() },
            )
        setStep(CheckStep.SERVER, StepState.RUNNING)
        try {
            api.health()
        } catch (error: ApiException) {
            return fail(CheckStep.SERVER, UserError.of(error.error))
        }
        setStep(CheckStep.SERVER, StepState.PASSED)

        setStep(CheckStep.TOKEN, StepState.RUNNING)
        if (token.isEmpty()) {
            return fail(
                CheckStep.TOKEN,
                UserError(
                    code = "TOKEN_MISSING",
                    message = "Enter the access token.",
                    hint = "Run `msj create-token` on the server and paste the token it prints.",
                    details = emptyList(),
                ),
            )
        }
        try {
            api.checkSession()
        } catch (error: ApiException) {
            return fail(CheckStep.TOKEN, UserError.of(error.error))
        }
        setStep(CheckStep.TOKEN, StepState.PASSED)

        if (!save) {
            mutableUi.update { it.copy(busy = false, verified = true) }
            return
        }
        services.session.signIn(server, token, serverIsPrivate)
        services.outbox.retryAllNow()
        mutableUi.update { it.copy(busy = false, verified = true, done = true, token = "") }
    }

    private fun localNetworkDenied(): UserError =
        UserError(
            code = "LOCAL_NETWORK_DENIED",
            message = "The journal server has a private-network address, and local network access is not allowed.",
            hint =
                "Allow it: Settings > Apps > Journal > Permissions > Nearby devices > Allow. " +
                    "Without it, Android blocks the connection to the server.",
            details = emptyList(),
        )
}
