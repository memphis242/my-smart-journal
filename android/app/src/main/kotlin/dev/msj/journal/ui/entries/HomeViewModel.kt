package dev.msj.journal.ui.entries

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.EntryFilters
import dev.msj.journal.core.api.EntrySummary
import dev.msj.journal.core.api.Facets
import dev.msj.journal.core.api.QueueSnapshot
import dev.msj.journal.core.api.SortOrder
import dev.msj.journal.core.dates.DateBounds
import dev.msj.journal.core.dates.RangeKey
import dev.msj.journal.core.dates.presetBounds
import dev.msj.journal.core.outbox.OutboxRecord
import dev.msj.journal.core.outbox.OutboxState
import dev.msj.journal.core.queue.QueueLogic
import dev.msj.journal.ui.UserError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** Which facet values the list is narrowed to; values of one kind are ORed, kinds are ANDed (as on the server). */
data class FacetSelection(
    val tags: Set<String> = emptySet(),
    val projects: Set<String> = emptySet(),
    val skills: Set<String> = emptySet(),
) {
    val size: Int get() = tags.size + projects.size + skills.size

    fun toggled(
        kind: FacetKind,
        value: String,
    ): FacetSelection {
        fun Set<String>.flip() = if (value in this) this - value else this + value
        return when (kind) {
            FacetKind.TAG -> copy(tags = tags.flip())
            FacetKind.PROJECT -> copy(projects = projects.flip())
            FacetKind.SKILL -> copy(skills = skills.flip())
        }
    }

    fun has(
        kind: FacetKind,
        value: String,
    ): Boolean =
        when (kind) {
            FacetKind.TAG -> value in tags
            FacetKind.PROJECT -> value in projects
            FacetKind.SKILL -> value in skills
        }
}

enum class FacetKind { TAG, PROJECT, SKILL }

data class FacetsState(
    val facets: Facets? = null,
    val loading: Boolean = false,
    val error: UserError? = null,
)

data class ListState(
    val range: RangeKey,
    val bounds: DateBounds,
    val selection: FacetSelection = FacetSelection(),
    val entries: List<EntrySummary> = emptyList(),
    val total: Int = 0,
    val loaded: Boolean = false,
    val loading: Boolean = false,
    val loadingMore: Boolean = false,
    val error: UserError? = null,
) {
    val complete: Boolean get() = loaded && entries.size >= total
    val filters: EntryFilters
        get() =
            EntryFilters(
                from = bounds.fromIso,
                to = bounds.toIso,
                tags = selection.tags.sorted(),
                projects = selection.projects.sorted(),
                skills = selection.skills.sorted(),
            )
}

data class QueueState(
    val snapshot: QueueSnapshot? = null,
    val fetchedAtMs: Long? = null,
    /** Server clock minus phone clock when the snapshot arrived. */
    val skewMs: Long = 0,
    val error: UserError? = null,
    val runPending: Boolean = false,
    val runError: UserError? = null,
    /** The last Run now was accepted, but no worker is running to pick it up. */
    val requestedWithStaleWorker: Boolean = false,
)

/** What the unsent strip shows. */
data class UnsentSummary(
    val total: Int,
    val rejected: Int,
)

/**
 * The entry list for a date range, the processing queue, and the unsent
 * outbox summary. The queue is polled only while the screen is visible
 * ([pollQueue] runs inside the screen's started lifecycle).
 */
class HomeViewModel(
    private val services: AppServices,
) : ViewModel() {
    private val mutableList =
        MutableStateFlow(
            RangeKey.entries.firstOrNull { it.name == services.prefs.range }.let { range ->
                val key = range ?: RangeKey.WEEK
                ListState(range = key, bounds = presetBounds(key, services.today()))
            },
        )
    val list: StateFlow<ListState> = mutableList.asStateFlow()

    private val mutableQueue = MutableStateFlow(QueueState())
    val queue: StateFlow<QueueState> = mutableQueue.asStateFlow()

    val unsent: StateFlow<UnsentSummary> =
        services.outbox.unsent
            .map { records: List<OutboxRecord> ->
                UnsentSummary(
                    records.size,
                    records.count {
                        it.state ==
                            OutboxState.FAILED
                    },
                )
            }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), UnsentSummary(0, 0))

    private val mutableMessages = MutableSharedFlow<String>(extraBufferCapacity = 4)

    /** Short confirmations for the snackbar. */
    val messages: SharedFlow<String> = mutableMessages.asSharedFlow()

    private var loadJob: Job? = null
    private var fastUntilMs = 0L
    private var lastSignature: String? = null
    private val pokes = Channel<Unit>(Channel.CONFLATED)

    init {
        refresh()
        viewModelScope.launch {
            var previous: Int? = null
            services.outbox.unsent.map { it.size }.distinctUntilChanged().collect { count ->
                // An upload finished: its entry is on the server now.
                val before = previous
                if (before != null && count < before) refresh()
                previous = count
            }
        }
    }

    fun selectRange(range: RangeKey) {
        services.prefs.range = range.name
        mutableList.value =
            ListState(
                range = range,
                bounds = presetBounds(range, services.today()),
                selection = mutableList.value.selection,
            )
        refresh()
    }

    private val mutableFacets = MutableStateFlow(FacetsState())
    val facets: StateFlow<FacetsState> = mutableFacets.asStateFlow()

    /** `GET /facets` for the filter sheet; reloaded each time the sheet opens so counts are current. */
    fun loadFacets() {
        mutableFacets.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            val api = services.session.current() ?: return@launch
            try {
                mutableFacets.value = FacetsState(facets = api.facets())
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: ApiException) {
                mutableFacets.update { it.copy(loading = false, error = UserError.of(error.error)) }
            }
        }
    }

    fun toggleFacet(
        kind: FacetKind,
        value: String,
    ) {
        mutableList.update {
            it.copy(
                selection = it.selection.toggled(kind, value),
                entries = emptyList(),
                loaded = false,
            )
        }
        refresh()
    }

    fun clearFacets() {
        if (mutableList.value.selection.size == 0) return
        mutableList.update { it.copy(selection = FacetSelection(), entries = emptyList(), loaded = false) }
        refresh()
    }

    /** Reloads the first page; today's bounds are recomputed so a list left open overnight moves on. */
    fun refresh() {
        loadJob?.cancel()
        mutableList.update { it.copy(bounds = presetBounds(it.range, services.today()), loading = true, error = null) }
        val state = mutableList.value
        loadJob =
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    val page = api.listEntries(state.filters, SortOrder.NEWEST_FIRST, PAGE, 0)
                    mutableList.update {
                        it.copy(
                            entries = page.items,
                            total = page.total,
                            loaded = true,
                            loading = false,
                        )
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: ApiException) {
                    mutableList.update { it.copy(loading = false, error = UserError.of(error.error)) }
                }
            }
    }

    fun loadMore() {
        val state = mutableList.value
        if (state.loading || state.loadingMore || state.complete || !state.loaded) return
        mutableList.update { it.copy(loadingMore = true) }
        loadJob =
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    val page = api.listEntries(state.filters, SortOrder.NEWEST_FIRST, PAGE, state.entries.size)
                    mutableList.update { current ->
                        val known = current.entries.mapTo(HashSet()) { it.id }
                        current.copy(
                            entries = current.entries + page.items.filter { it.id !in known },
                            total = page.total,
                            loadingMore = false,
                        )
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: ApiException) {
                    mutableList.update { it.copy(loadingMore = false, error = UserError.of(error.error)) }
                }
            }
    }

    /**
     * The screen came into view: reload when the last load failed (for example after signing in
     * again) or when the day changed since, so the range still ends today.
     */
    fun onVisible() {
        val state = mutableList.value
        if (state.error != null || state.bounds != presetBounds(state.range, services.today())) refresh()
    }

    /** Polls `GET /queue` at the pace the queue needs, until the caller's scope is cancelled. */
    suspend fun pollQueue() {
        while (true) {
            refreshQueue()
            val state = mutableQueue.value
            val now = services.clock()
            val delayMs = QueueLogic.pollDelayMs(state.snapshot, now, state.skewMs, state.fetchedAtMs, fastUntilMs)
            withTimeoutOrNull(delayMs) { pokes.receive() }
        }
    }

    fun isWorkerStale(state: QueueState): Boolean {
        val snapshot = state.snapshot ?: return false
        val now = services.clock()
        return QueueLogic.workerStale(snapshot, now, state.skewMs, state.fetchedAtMs ?: now)
    }

    /** `POST /batches/run`: 202 (possibly with a stopped worker) or an error with its code. */
    fun runNow() {
        if (mutableQueue.value.runPending) return
        mutableQueue.update { it.copy(runPending = true, runError = null, requestedWithStaleWorker = false) }
        viewModelScope.launch {
            try {
                val api = services.session.current() ?: return@launch
                val result = api.runBatch()
                val stale = result.worker?.stale == true
                if (!stale) {
                    fastUntilMs = QueueLogic.fastUntilAfterRequest(services.clock())
                    mutableMessages.tryEmit("Batch requested; the worker starts it within a few seconds")
                }
                mutableQueue.update { it.copy(requestedWithStaleWorker = stale) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: ApiException) {
                mutableQueue.update { it.copy(runError = UserError.of(error.error)) }
            } finally {
                mutableQueue.update { it.copy(runPending = false) }
                pokes.trySend(Unit)
            }
        }
    }

    fun dismissRunError() = mutableQueue.update { it.copy(runError = null, requestedWithStaleWorker = false) }

    private suspend fun refreshQueue() {
        val api = services.session.current() ?: return
        try {
            val status = api.queue()
            val received = services.clock()
            val signature = signature(status.snapshot)
            val moved = lastSignature != null && signature != lastSignature
            lastSignature = signature
            mutableQueue.update {
                it.copy(
                    snapshot = status.snapshot,
                    fetchedAtMs = received,
                    skewMs = status.serverTimeMs?.let { server -> server - received } ?: 0L,
                    error = null,
                )
            }
            if (moved) refresh()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ApiException) {
            mutableQueue.update { it.copy(error = UserError.of(error.error)) }
        }
    }

    private fun signature(snapshot: QueueSnapshot): String =
        buildString {
            append(snapshot.batch.running).append('|').append(snapshot.batch.lastFinishedAt).append('|')
            snapshot.items.joinTo(this, ",") { "${it.jobId}:${it.status}" }
        }

    private companion object {
        const val PAGE = 100
    }
}
