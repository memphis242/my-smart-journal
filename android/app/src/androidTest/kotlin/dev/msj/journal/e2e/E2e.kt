package dev.msj.journal.e2e

import android.Manifest
import android.content.Context
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
import dev.msj.journal.harness.E2eConfig
import dev.msj.journal.session.ParsedServer
import dev.msj.journal.ui.components.CountKey
import dev.msj.journal.ui.components.EntryIdKey
import kotlinx.coroutines.runBlocking
import org.junit.rules.RuleChain
import org.junit.rules.TestRule
import java.time.LocalDate

/** Shared helpers for the journeys; the backend URL, token and local-network grant come from [E2eConfig]. */
object E2e {
    val serverUrl: String get() = E2eConfig.serverUrl

    val token: String get() = E2eConfig.token

    val services: AppServices get() = ApplicationProvider.getApplicationContext<Context>().appServices

    /** An API client for checking server state; the same code path the app uses. */
    val api: JournalApi by lazy {
        val parsed = ServerUrl.parse(serverUrl, setOf("10.0.2.2", "127.0.0.1"))
        check(parsed is ServerUrlResult.Valid) { "msjServerUrl $serverUrl is not a valid server address" }
        JournalApiClient(parsed.url, token, JournalHttp.defaultClient())
    }

    /** Permissions the journeys need: the microphone, and local network access on Android 17. */
    fun permissions(): TestRule =
        RuleChain
            .outerRule(
                GrantPermissionRule.grant(Manifest.permission.RECORD_AUDIO),
            ).around(E2eConfig.localNetworkRule())

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
        node.config.getOrElseNullable(SemanticsProperties.TestTag) { null }?.startsWith(prefix) == true
    }
