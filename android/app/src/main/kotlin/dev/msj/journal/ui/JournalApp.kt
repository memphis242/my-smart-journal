package dev.msj.journal.ui

import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NavDestination.Companion.hasRoute
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.toRoute
import dev.msj.journal.AppServices
import dev.msj.journal.appServices
import dev.msj.journal.session.LocalNetworkAccess
import dev.msj.journal.session.SessionManager
import dev.msj.journal.ui.capture.ComposerScreen
import dev.msj.journal.ui.capture.ComposerViewModel
import dev.msj.journal.ui.capture.RecorderScreen
import dev.msj.journal.ui.capture.RecorderViewModel
import dev.msj.journal.ui.components.ThemeSwitch
import dev.msj.journal.ui.entries.DetailScreen
import dev.msj.journal.ui.entries.DetailViewModel
import dev.msj.journal.ui.entries.EditorScreen
import dev.msj.journal.ui.entries.EditorViewModel
import dev.msj.journal.ui.entries.HomeScreen
import dev.msj.journal.ui.entries.HomeViewModel
import dev.msj.journal.ui.entries.NavContext
import dev.msj.journal.ui.nav.ComposerRoute
import dev.msj.journal.ui.nav.EditorRoute
import dev.msj.journal.ui.nav.EntryRoute
import dev.msj.journal.ui.nav.HomeRoute
import dev.msj.journal.ui.nav.LicensesRoute
import dev.msj.journal.ui.nav.OutboxRoute
import dev.msj.journal.ui.nav.RecorderRoute
import dev.msj.journal.ui.nav.SearchRoute
import dev.msj.journal.ui.nav.SettingsRoute
import dev.msj.journal.ui.nav.SignInRoute
import dev.msj.journal.ui.outbox.OutboxScreen
import dev.msj.journal.ui.outbox.OutboxViewModel
import dev.msj.journal.ui.search.SearchScreen
import dev.msj.journal.ui.search.SearchViewModel
import dev.msj.journal.ui.settings.LicensesScreen
import dev.msj.journal.ui.settings.SettingsScreen
import dev.msj.journal.ui.settings.SettingsViewModel
import dev.msj.journal.ui.signin.SignInScreen
import dev.msj.journal.ui.signin.SignInViewModel
import dev.msj.journal.ui.theme.Journal
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/** What the app was opened to do: a home-screen shortcut or text shared from another app. */
sealed interface LaunchAction {
    data object Record : LaunchAction

    data class Write(
        val sharedText: String?,
    ) : LaunchAction
}

/** Activity-scoped state shared between destinations. */
class ShellViewModel : ViewModel() {
    /** The list each opened entry came from, keyed by the entry id it was opened at. */
    val contexts: MutableMap<String, NavContext> = HashMap()
}

@Composable
inline fun <reified VM : ViewModel> appViewModel(
    key: String? = null,
    crossinline create: (AppServices, SavedStateHandle) -> VM,
): VM {
    val services = LocalContext.current.appServices
    return viewModel(
        key = key,
        factory = viewModelFactory { initializer { create(services, createSavedStateHandle()) } },
    )
}

@Composable
fun JournalApp(
    services: AppServices,
    theme: ThemeController,
    launches: StateFlow<LaunchAction?>,
    onConsumeLaunch: () -> Unit,
    modifier: Modifier = Modifier,
    shell: ShellViewModel = viewModel(),
) {
    val session by services.session.state.collectAsStateWithLifecycle()
    val c = Journal.colors
    Box(modifier.fillMaxSize().background(c.bg)) {
        if (session == SessionManager.State.Loading) return@Box
        val nav = rememberNavController()
        val start = remember { if (session is SessionManager.State.SignedIn) HomeRoute else SignInRoute() }
        val snackbar = remember { SnackbarHostState() }
        val scope = rememberCoroutineScope()
        val activity = LocalActivity.current
        val dark = theme.isDark()
        val themeSwitch: @Composable () -> Unit = {
            ThemeSwitch(dark = dark, onToggle = { if (activity != null) theme.toggle(activity, dark) })
        }

        LaunchedEffect(session) {
            if (session is SessionManager.State.SignedOut &&
                nav.currentDestination?.hasRoute(SignInRoute::class) != true
            ) {
                nav.navigate(SignInRoute()) { popUpTo(nav.graph.id) { inclusive = true } }
            }
        }
        val pending by launches.collectAsStateWithLifecycle()
        val latestConsumeLaunch by rememberUpdatedState(onConsumeLaunch)
        LaunchedEffect(pending, session) {
            val action = pending ?: return@LaunchedEffect
            if (session !is SessionManager.State.SignedIn) return@LaunchedEffect
            latestConsumeLaunch()
            when (action) {
                LaunchAction.Record -> nav.navigate(RecorderRoute) { launchSingleTop = true }
                is LaunchAction.Write -> nav.navigate(ComposerRoute(action.sharedText)) { launchSingleTop = true }
            }
        }
        // Uploads restart whenever the app comes to the foreground (for example after NetBird reconnects).
        val lifecycle = LocalLifecycleOwner.current.lifecycle
        LaunchedEffect(lifecycle) { lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { services.outbox.kick() } }

        val savedNotice: (Boolean, String) -> Unit = { saved, what ->
            nav.popBackStack()
            if (saved) {
                scope.launch {
                    snackbar.showSnackbar(
                        "$what saved on this phone; it uploads when the server is reachable",
                    )
                }
            }
        }

        NavHost(nav, startDestination = start, modifier = Modifier.fillMaxSize()) {
            composable<SignInRoute> { entry ->
                val route = entry.toRoute<SignInRoute>()
                val context = LocalContext.current
                val vm = appViewModel { s, _ -> SignInViewModel(s) { LocalNetworkAccess.granted(context) } }
                SignInScreen(
                    viewModel = vm,
                    again = route.again,
                    onDone = {
                        if (route.again) {
                            nav.popBackStack()
                        } else {
                            nav.navigate(HomeRoute) { popUpTo(nav.graph.id) { inclusive = true } }
                        }
                    },
                    onBack = if (route.again) ({ nav.popBackStack() }) else null,
                    themeSwitch = themeSwitch,
                )
            }
            composable<HomeRoute> {
                val vm = appViewModel { s, _ -> HomeViewModel(s) }
                val signedIn = session as? SessionManager.State.SignedIn
                val context = LocalContext.current
                var localGranted by rememberSaveable { mutableStateOf(LocalNetworkAccess.granted(context)) }
                val permission =
                    rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
                        localGranted = granted
                        if (granted) scope.launch { services.outbox.retryAllNow() }
                    }
                HomeScreen(
                    viewModel = vm,
                    host = signedIn?.host.orEmpty(),
                    localNetworkBlocked = LocalNetworkAccess.applies && services.prefs.serverIsPrivate && !localGranted,
                    onAllowLocalNetwork = { permission.launch(LocalNetworkAccess.PERMISSION) },
                    onOpenEntry = { id, context ->
                        shell.contexts[id] = context
                        nav.navigate(EntryRoute(id))
                    },
                    onSearch = { nav.navigate(SearchRoute) },
                    onWrite = { nav.navigate(ComposerRoute()) },
                    onRecord = { nav.navigate(RecorderRoute) },
                    onOutbox = { nav.navigate(OutboxRoute) },
                    onSettings = { nav.navigate(SettingsRoute) },
                    onSignInAgain = { nav.navigate(SignInRoute(again = true)) },
                    themeSwitch = themeSwitch,
                    snackbar = snackbar,
                )
            }
            composable<EntryRoute> { entry ->
                val id = entry.toRoute<EntryRoute>().id
                val context = shell.contexts[id] ?: NavContext.single(id)
                val vm = appViewModel { s, _ -> DetailViewModel(s, id, context) }
                LaunchedEffect(entry) {
                    // Back from the editor: show what was saved.
                    entry.savedStateHandle.getStateFlow("edited", false).collect { edited ->
                        if (edited) {
                            entry.savedStateHandle["edited"] = false
                            vm.reload()
                        }
                    }
                }
                DetailScreen(
                    viewModel = vm,
                    onBack = { nav.popBackStack() },
                    onEdit = { editId -> nav.navigate(EditorRoute(editId)) },
                    onSignInAgain = { nav.navigate(SignInRoute(again = true)) },
                )
            }
            composable<EditorRoute> { entry ->
                val id = entry.toRoute<EditorRoute>().id
                val vm = appViewModel { s, _ -> EditorViewModel(s, id) }
                EditorScreen(vm, onClose = { saved ->
                    if (saved) nav.previousBackStackEntry?.savedStateHandle?.set("edited", true)
                    nav.popBackStack()
                })
            }
            composable<ComposerRoute> { entry ->
                val shared = entry.toRoute<ComposerRoute>().sharedText
                val vm = appViewModel { s, handle -> ComposerViewModel(s, handle, shared) }
                ComposerScreen(vm, today = services.today(), onClose = { saved -> savedNotice(saved, "Entry") })
            }
            composable<RecorderRoute> {
                val vm = appViewModel { s, handle -> RecorderViewModel(s, handle) }
                RecorderScreen(vm, onClose = { saved -> savedNotice(saved, "Recording") })
            }
            composable<OutboxRoute> {
                val vm = appViewModel { s, _ -> OutboxViewModel(s) }
                OutboxScreen(vm, onBack = { nav.popBackStack() })
            }
            composable<SearchRoute> {
                val vm = appViewModel { s, _ -> SearchViewModel(s) }
                SearchScreen(vm, onBack = { nav.popBackStack() }, onOpenEntry = { id, context ->
                    shell.contexts[id] = context
                    nav.navigate(EntryRoute(id))
                })
            }
            composable<SettingsRoute> {
                val vm = appViewModel { s, _ -> SettingsViewModel(s) }
                SettingsScreen(vm, onBack = { nav.popBackStack() }, onLicenses = { nav.navigate(LicensesRoute) })
            }
            composable<LicensesRoute> { LicensesScreen(onBack = { nav.popBackStack() }) }
        }
    }
}
