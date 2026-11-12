package dev.msj.journal.e2e

import android.Manifest
import android.content.Context
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.junit4.ComposeTestRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.core.app.ApplicationProvider
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import dev.msj.journal.AppServices
import dev.msj.journal.appServices
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.JournalApi
import dev.msj.journal.core.api.JournalApiClient
import dev.msj.journal.core.api.JournalHttp
import dev.msj.journal.core.api.ServerUrl
import dev.msj.journal.core.api.ServerUrlResult
import dev.msj.journal.core.dates.isoDate
import dev.msj.journal.session.ParsedServer
import dev.msj.journal.ui.components.CountKey
import dev.msj.journal.ui.components.EntryIdKey
import kotlinx.coroutines.runBlocking
import java.io.FileInputStream
import java.time.LocalDate

/**
 * The real backend the tests talk to (started by scripts/e2e.sh): its URL from
 * the `msjServerUrl` instrumentation argument, and the access token read from
 * the device file the script pushed. The token is read through the shell user
 * (the app cannot read /data/local/tmp) and never printed.
 */
object E2e {
    private const val TOKEN_FILE = "/data/local/tmp/msj-e2e-token"
    private const val DEFAULT_URL = "http://10.0.2.2:18780"

    val serverUrl: String by lazy {
        InstrumentationRegistry.getArguments().getString("msjServerUrl")?.takeIf { it.isNotBlank() } ?: DEFAULT_URL
    }

    val token: String by lazy {
        val pfd = InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("cat $TOKEN_FILE")
        val text =
            ParcelFileDescriptor
                .AutoCloseInputStream(pfd)
                .use {
                    FileInputStream(it.fd).bufferedReader().readText()
                }.trim()
        check(text.isNotEmpty() && !text.contains("No such file")) {
            "No e2e token at $TOKEN_FILE on the device; run android/scripts/e2e.sh, which pushes it"
        }
        text
    }

    val services: AppServices get() = ApplicationProvider.getApplicationContext<Context>().appServices

    /** An API client for checking server state; the same code path the app uses. */
    val api: JournalApi by lazy {
        val parsed = ServerUrl.parse(serverUrl, setOf("10.0.2.2", "127.0.0.1"))
        check(parsed is ServerUrlResult.Valid) { "msjServerUrl $serverUrl is not a valid server address" }
        JournalApiClient(parsed.url, token, JournalHttp.defaultClient())
    }

    /** Permissions the journeys need: the microphone, and local network access on Android 17. */
    fun permissions(): GrantPermissionRule {
        val wanted = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >=
            Build.VERSION_CODES.CINNAMON_BUN
        ) {
            wanted += "android.permission.ACCESS_LOCAL_NETWORK"
        }
        return GrantPermissionRule.grant(*wanted.toTypedArray())
    }

    /** Signs in without the UI (for journeys that test something else); call before launching the activity. */
    fun signInDirectly() {
        val parsed = services.connections.parse(serverUrl)
        check(parsed is ParsedServer.Valid) { "msjServerUrl $serverUrl is not a valid server address" }
        runBlocking { services.session.signIn(parsed, token, serverIsPrivate = false) }
    }

    val today: String get() = isoDate(LocalDate.now())

    /** The newest entry on [date] that [match] accepts, or null. */
    fun findEntry(
        date: String = today,
        match: (EntryDetail) -> Boolean,
    ): EntryDetail? =
        runBlocking {
            api.listEntriesFull(EntryFilters(from = date, to = date), limit = 500, offset = 0).items.lastOrNull(match)
        }
}

fun ComposeTestRule.waitForTag(
    tag: String,
    timeoutMs: Long = 20_000,
) = waitUntil(timeoutMs) { onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty() }

fun ComposeTestRule.waitForNoTag(
    tag: String,
    timeoutMs: Long = 20_000,
) = waitUntil(timeoutMs) { onAllNodesWithTag(tag).fetchSemanticsNodes().isEmpty() }

/** Waits until a node shows entry [id] (a list row, a detail body). */
fun ComposeTestRule.waitForEntry(
    id: String,
    timeoutMs: Long = 20_000,
) = waitUntil(timeoutMs) { onAllNodes(SemanticsMatcher.expectValue(EntryIdKey, id)).fetchSemanticsNodes().isNotEmpty() }

/** The count a status display reports through [CountKey]. */
fun ComposeTestRule.countOf(tag: String): Int =
    onNodeWithTag(tag, useUnmergedTree = false).fetchSemanticsNode().config[CountKey]

fun SemanticsNodeInteraction.isEnabled(): Boolean = !fetchSemanticsNode().config.contains(SemanticsProperties.Disabled)

/** Nodes whose test tag starts with [prefix] (rows tagged with their entry id). */
fun hasTagPrefix(prefix: String): SemanticsMatcher =
    SemanticsMatcher("test tag starts with $prefix") { node ->
        node.config.getOrNull(SemanticsProperties.TestTag)?.startsWith(prefix) == true
    }
