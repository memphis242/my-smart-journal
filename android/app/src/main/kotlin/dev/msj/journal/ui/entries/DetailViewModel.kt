package dev.msj.journal.ui.entries

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.msj.journal.AppServices
import dev.msj.journal.core.api.ApiException
import dev.msj.journal.core.api.EntryDetail
import dev.msj.journal.core.api.EntrySummary
import dev.msj.journal.ui.UserError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class DetailUi(
    val id: String,
    /** -1 moved to an older entry, 1 to a newer one, 0 opened directly. */
    val direction: Int = 0,
    val preview: EntrySummary? = null,
    val detail: EntryDetail? = null,
    val loading: Boolean = true,
    val error: UserError? = null,
    val newer: String? = null,
    val older: String? = null,
    val neighborsLoading: Boolean = false,
    val neighborsError: UserError? = null,
) {
    /** What to render: the full detail when loaded, else the list's summary. */
    val summary: EntrySummary? get() = detail?.toSummary() ?: preview
}

/**
 * One entry and its neighbors in the list it was opened from. Stepping uses
 * the list's own order; past the loaded rows of an incomplete list it asks
 * `GET /entries/{id}/neighbors` with the list's filters, like the web client.
 */
class DetailViewModel(
    private val services: AppServices,
    startId: String,
    private val context: NavContext,
) : ViewModel() {
    private val mutableUi = MutableStateFlow(DetailUi(id = startId))
    val ui: StateFlow<DetailUi> = mutableUi.asStateFlow()
    private val cache =
        object : LinkedHashMap<String, EntryDetail>(MAX_CACHED, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, EntryDetail>): Boolean =
                size > MAX_CACHED
        }
    private var detailJob: Job? = null
    private var neighborsJob: Job? = null
    private val serverNeighbors = HashMap<String, Pair<String?, String?>>()

    init {
        show(startId, 0)
    }

    /** Steps to the next newer ([step] = 1) or older ([step] = -1) entry, when there is one. */
    fun go(step: Int) {
        assert(step == 1 || step == -1) { "step is one entry newer or older" }
        val current = mutableUi.value
        val target = if (step == 1) current.newer else current.older
        if (target != null) show(target, step)
    }

    fun reload() {
        cache.remove(mutableUi.value.id)
        show(mutableUi.value.id, 0)
    }

    private fun show(
        id: String,
        direction: Int,
    ) {
        val local = context.local(id)
        val known = serverNeighbors[id]
        val cached = cache[id]
        mutableUi.value =
            DetailUi(
                id = id,
                direction = direction,
                preview = context.previews[id] ?: cached?.toSummary(),
                detail = cached,
                loading = cached == null,
                newer = local.newer ?: known?.first,
                older = local.older ?: known?.second,
            )
        loadDetail(id, refresh = cached != null)
        if (context.needsServer(id) && known == null) loadNeighbors(id)
    }

    private fun loadDetail(
        id: String,
        refresh: Boolean,
    ) {
        detailJob?.cancel()
        detailJob =
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    val detail = api.getEntry(id)
                    cache[id] = detail
                    mutableUi.update {
                        if (it.id ==
                            id
                        ) {
                            it.copy(detail = detail, loading = false, error = null)
                        } else {
                            it
                        }
                    }
                    prefetchNeighbors()
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: ApiException) {
                    // A cached copy stays on screen when a refresh fails; the notice says the refresh failed.
                    mutableUi.update {
                        if (it.id ==
                            id
                        ) {
                            it.copy(loading = false, error = UserError.of(error.error))
                        } else {
                            it
                        }
                    }
                    if (!refresh) return@launch
                }
            }
    }

    private fun loadNeighbors(id: String) {
        neighborsJob?.cancel()
        mutableUi.update { it.copy(neighborsLoading = true, neighborsError = null) }
        neighborsJob =
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    val found = api.neighbors(id, context.filters)
                    serverNeighbors[id] = found.newer to found.older
                    mutableUi.update {
                        if (it.id != id) {
                            it
                        } else {
                            it.copy(
                                newer = it.newer ?: found.newer,
                                older = it.older ?: found.older,
                                neighborsLoading = false,
                            )
                        }
                    }
                    prefetchNeighbors()
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: ApiException) {
                    mutableUi.update {
                        if (it.id ==
                            id
                        ) {
                            it.copy(neighborsLoading = false, neighborsError = UserError.of(error.error))
                        } else {
                            it
                        }
                    }
                }
            }
    }

    /** Fetches the neighbors' details ahead of a step, so the next swipe renders at once. Failures are retried on arrival. */
    private fun prefetchNeighbors() {
        val current = mutableUi.value
        listOfNotNull(current.newer, current.older).filter { it !in cache }.forEach { id ->
            viewModelScope.launch {
                val api = services.session.current() ?: return@launch
                try {
                    cache[id] = api.getEntry(id)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: ApiException) {
                    // Only a prefetch: stepping to this entry loads it again and shows any error then.
                }
            }
        }
    }

    private companion object {
        const val MAX_CACHED = 40
    }
}
