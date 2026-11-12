package dev.msj.journal.ui.signin

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.msj.journal.session.LocalNetworkAccess
import dev.msj.journal.ui.UserError
import dev.msj.journal.ui.components.ErrorNotice
import dev.msj.journal.ui.components.IconAction
import dev.msj.journal.ui.components.InlineAction
import dev.msj.journal.ui.components.JournalField
import dev.msj.journal.ui.components.JournalIcons
import dev.msj.journal.ui.components.MarkerKind
import dev.msj.journal.ui.components.NoticeTone
import dev.msj.journal.ui.components.PrimaryButton
import dev.msj.journal.ui.components.SecondaryButton
import dev.msj.journal.ui.components.SectionLabel
import dev.msj.journal.ui.components.StatusMarker
import dev.msj.journal.ui.components.TestTags
import dev.msj.journal.ui.components.TopBar
import dev.msj.journal.ui.components.stateName
import dev.msj.journal.ui.theme.Journal
import dev.msj.journal.ui.theme.Space

@Composable
fun SignInScreen(
    viewModel: SignInViewModel,
    again: Boolean,
    onDone: () -> Unit,
    onBack: (() -> Unit)?,
    themeSwitch: @Composable () -> Unit,
    modifier: Modifier = Modifier,
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var showToken by rememberSaveable { mutableStateOf(false) }
    val permission =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            viewModel.onLocalNetworkResult(granted)
        }
    val latestOnDone by rememberUpdatedState(onDone)
    LaunchedEffect(ui.done) { if (ui.done) latestOnDone() }
    val c = Journal.colors

    Column(modifier.fillMaxSize().testTag(TestTags.SIGN_IN_SCREEN)) {
        TopBar(
            title = if (again) "Sign in again" else "Sign in",
            navigation = onBack?.let { back -> { IconAction(JournalIcons.Back, "Back", back) } },
            actions = { themeSwitch() },
        )
        Column(
            Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = Space.gutter, vertical = Space.s4),
            verticalArrangement = Arrangement.spacedBy(Space.s4),
        ) {
            Text(
                "Connect to your journal server. It is reachable only through NetBird, so connect NetBird first.",
                style = Journal.type.ui,
                color = c.ink2,
            )
            JournalField(
                value = ui.url,
                onValueChange = viewModel::onUrlChange,
                label = "Server address",
                placeholder = "https://journal.example.com",
                supporting = "The address you open the journal at in a browser.",
                keyboardType = KeyboardType.Uri,
                modifier = Modifier.testTag(TestTags.SIGN_IN_URL),
            )
            JournalField(
                value = ui.token,
                onValueChange = viewModel::onTokenChange,
                label = "Access token",
                supporting = "Printed by `msj create-token` on the server. Stored encrypted on this phone.",
                keyboardType = KeyboardType.Password,
                textStyle = Journal.type.mono,
                visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
                modifier = Modifier.testTag(TestTags.SIGN_IN_TOKEN),
                trailing = {
                    InlineAction(if (showToken) "Hide" else "Show", onClick = { showToken = !showToken })
                },
            )

            Checklist(ui)

            if (ui.needsLocalNetwork && ui.error == null) {
                ErrorNotice(
                    error =
                        UserError(
                            code = "LOCAL_NETWORK_PERMISSION",
                            message =
                                "The server has a private-network address. Android needs your permission to connect to it.",
                            hint =
                                "Allow access to devices on the local network (Nearby devices). " +
                                    "The app connects only to your journal server.",
                            details = emptyList(),
                        ),
                    tone = NoticeTone.WARNING,
                ) {
                    InlineAction(
                        "Allow local network access",
                        onClick = { permission.launch(LocalNetworkAccess.PERMISSION) },
                        modifier = Modifier.testTag(TestTags.SIGN_IN_ALLOW_LOCAL_NETWORK),
                    )
                }
            }
            ui.error?.let { error ->
                ErrorNotice(error) {
                    if (error.code == "LOCAL_NETWORK_DENIED") {
                        InlineAction("Ask again", onClick = { permission.launch(LocalNetworkAccess.PERMISSION) })
                    }
                }
            }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.navigationBars)
                .imePadding()
                .padding(horizontal = Space.gutter, vertical = Space.s3),
            horizontalArrangement = Arrangement.spacedBy(Space.s3, Alignment.End),
        ) {
            SecondaryButton(
                text = "Test connection",
                onClick = viewModel::testConnection,
                enabled = !ui.busy,
                modifier = Modifier.testTag(TestTags.SIGN_IN_TEST),
            )
            PrimaryButton(
                text = "Sign in",
                onClick = viewModel::signIn,
                enabled = !ui.busy,
                modifier = Modifier.testTag(TestTags.SIGN_IN_SUBMIT),
            )
        }
    }
}

@Composable
private fun Checklist(ui: SignInUi) {
    val c = Journal.colors
    Column(verticalArrangement = Arrangement.spacedBy(Space.s2)) {
        SectionLabel("Connection")
        CheckStep.entries.forEach { step ->
            val state = ui.steps.getValue(step)
            val (marker, status) =
                when (state) {
                    StepState.WAITING -> MarkerKind.QUEUED to "Not checked"
                    StepState.RUNNING -> MarkerKind.PROGRESS to "Checking"
                    StepState.PASSED -> MarkerKind.DONE to "Passed"
                    StepState.FAILED -> MarkerKind.FAILED to "Failed"
                    StepState.SKIPPED -> MarkerKind.MUTED to "Not needed"
                }
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 28.dp)
                    .testTag(TestTags.SIGN_IN_STEP_PREFIX + step.name)
                    .semantics { stateName = state.name },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Space.s2),
            ) {
                StatusMarker(marker, label = null)
                Text(stepLabel(step), style = Journal.type.ui, color = c.ink, modifier = Modifier.weight(1f))
                Text(status, style = Journal.type.monoSmall, color = c.ink3)
            }
        }
    }
}

private fun stepLabel(step: CheckStep): String =
    when (step) {
        CheckStep.ADDRESS -> "Address is valid"
        CheckStep.LOCAL_NETWORK -> "Local network access"
        CheckStep.SERVER -> "Journal server answers"
        CheckStep.TOKEN -> "Token accepted"
    }
