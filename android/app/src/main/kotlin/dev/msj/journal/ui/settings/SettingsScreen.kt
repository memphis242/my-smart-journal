package dev.msj.journal.ui.settings

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.session.LocalNetworkAccess
import dev.msj.journal.session.SessionManager
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.ConfirmDialog
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.MarkerKind
import dev.msj.journal.ui.components.Rule
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.stateName
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class TestResult { IDLE, RUNNING, PASSED, FAILED }

class SettingsViewModel(
    private val services: AppServices,
) : ViewModel() {
    val unsentCount: StateFlow<Int> =
        services.outbox.unsent
            .map { it.size }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    private val mutableTest = MutableStateFlow(TestResult.IDLE to (null as UserError?))
    val test: StateFlow<Pair<TestResult, UserError?>> = mutableTest.asStateFlow()

    private val mutableSignOutError = MutableStateFlow<UserError?>(null)
    val signOutError: StateFlow<UserError?> = mutableSignOutError.asStateFlow()

    val server: String? get() = (services.session.state.value as? SessionManager.State.SignedIn)?.server
    val serverIsPrivate: Boolean get() = services.prefs.serverIsPrivate

    /** `GET /health` then `GET /session`: two small requests, no journal content. */
    fun testConnection() {
        mutableTest.value = TestResult.RUNNING to null
        viewModelScope.launch {
            val api = services.session.current() ?: return@launch
            try {
                api.health()
                api.checkSession()
                mutableTest.value = TestResult.PASSED to null
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: ApiException) {
                mutableTest.value = TestResult.FAILED to UserError.of(error.error)
            }
        }
    }

    /** Deletes unsent entries and recordings, then the token, its key and the server address. */
    fun signOut() {
        viewModelScope.launch {
            try {
                services.outbox.wipe()
                services.session.signOut()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                mutableSignOutError.value = UserError.unexpected("Sign out", error).copy(code = "SIGN_OUT_FAILED")
            }
        }
    }
}

@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onBack: () -> Unit,
    onLicenses: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val unsent by viewModel.unsentCount.collectAsStateWithLifecycle()
    val test by viewModel.test.collectAsStateWithLifecycle()
    val signOutError by viewModel.signOutError.collectAsStateWithLifecycle()
    var confirmSignOut by rememberSaveable { mutableStateOf(false) }
    val context = LocalContext.current
    var localGranted by rememberSaveable { mutableStateOf(LocalNetworkAccess.granted(context)) }
    val permission =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { localGranted = it }
    val c = Journal.colors

    Column(modifier.fillMaxSize()) {
        TopBar(title = "Settings", navigation = { IconAction(JournalIcons.Back, "Back", onBack) })
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(Space.gutter),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
                SectionLabel("Server")
                Text(viewModel.server ?: "Not signed in", style = Journal.type.mono, color = c.ink)
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Space.s3),
                ) {
                    SecondaryButton(
                        "Test connection",
                        viewModel::testConnection,
                        enabled =
                            test.first != TestResult.RUNNING,
                    )
                    val (marker, label) =
                        when (test.first) {
                            TestResult.IDLE -> MarkerKind.QUEUED to "Not tested"
                            TestResult.RUNNING -> MarkerKind.PROGRESS to "Testing"
                            TestResult.PASSED -> MarkerKind.DONE to "Server and token OK"
                            TestResult.FAILED -> MarkerKind.FAILED to "Failed"
                        }
                    Row(
                        Modifier.semantics { stateName = test.first.name },
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(Space.s2),
                    ) {
                        StatusMarker(marker, label = null)
                        Text(label, style = Journal.type.uiSmall, color = c.ink2)
                    }
                }
                Text(
                    "Sends two small requests (health and sign-in check); no journal content.",
                    style = Journal.type.uiSmall,
                    color = c.ink3,
                )
                test.second?.let { ErrorNotice(it) }
            }
            if (LocalNetworkAccess.applies) {
                Rule()
                Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
                    SectionLabel("Local network access")
                    Text(
                        when {
                            localGranted -> {
                                "Allowed."
                            }

                            viewModel.serverIsPrivate -> {
                                "Not allowed. The server has a private-network address, so Android blocks it."
                            }

                            else -> {
                                "Not allowed. Not needed for this server's address."
                            }
                        },
                        style = Journal.type.ui,
                        color = c.ink2,
                    )
                    if (!localGranted) {
                        InlineAction(
                            "Allow",
                            onClick = { permission.launch(LocalNetworkAccess.PERMISSION) },
                        )
                    }
                }
            }
            Rule()
            Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
                SectionLabel("Fonts")
                Text(
                    "IBM Plex Sans, IBM Plex Mono and Source Serif 4, under the SIL Open Font License 1.1.",
                    style = Journal.type.ui,
                    color = c.ink2,
                )
                InlineAction("Show licenses", onClick = onLicenses)
            }
            Rule()
            Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
                SectionLabel("Sign out")
                Text(
                    "Deletes the access token and everything this app keeps on the phone. Entries already on the server stay there.",
                    style = Journal.type.ui,
                    color = c.ink2,
                )
                SecondaryButton(
                    "Sign out",
                    onClick = { confirmSignOut = true },
                    danger = true,
                    icon = JournalIcons.SignOut,
                    modifier = Modifier.testTag(TestTags.MENU_SIGN_OUT),
                )
                signOutError?.let { ErrorNotice(it) }
            }
        }
    }
    if (confirmSignOut) {
        ConfirmDialog(
            title = "Sign out of this phone?",
            text =
                if (unsent == 0) {
                    "The token and the server address are deleted from this phone. Nothing is waiting to upload."
                } else {
                    "The token and the server address are deleted, and $unsent " +
                        "${if (unsent == 1) "entry that has" else "entries that have"} not reached the server " +
                        "${if (unsent == 1) "is" else "are"} deleted for good."
                },
            action = if (unsent == 0) "Sign out" else "Delete $unsent and sign out",
            danger = true,
            onConfirm = {
                confirmSignOut = false
                viewModel.signOut()
            },
            onDismiss = { confirmSignOut = false },
        )
    }
}

@Composable
fun LicensesScreen(
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val texts =
        rememberSaveable {
            context.assets.list("licenses").orEmpty().sorted().map { name ->
                name to
                    context.assets
                        .open("licenses/$name")
                        .bufferedReader()
                        .use { it.readText() }
            }
        }
    val c = Journal.colors
    Column(modifier.fillMaxSize()) {
        TopBar(title = "Font licenses", navigation = { IconAction(JournalIcons.Back, "Back", onBack) })
        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(Space.gutter),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            texts.forEach { (name, text) ->
                SectionLabel(name.removeSuffix(".txt"))
                Text(text, style = Journal.type.monoSmall, color = c.ink2)
            }
        }
    }
}
