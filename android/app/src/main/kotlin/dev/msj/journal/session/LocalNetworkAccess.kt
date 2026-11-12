package dev.msj.journal.session

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import dev.msj.journal.core.net.PrivateAddresses
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.withTimeoutOrNull
import java.net.InetAddress
import java.net.UnknownHostException

/**
 * Android 17 (API 37) blocks connections to local-network addresses for apps
 * targeting it unless ACCESS_LOCAL_NETWORK is granted. A journal server on a
 * mesh VPN can resolve to such an address, so the app asks for the permission
 * only when the server's address is private.
 */
object LocalNetworkAccess {
    /** `Manifest.permission.ACCESS_LOCAL_NETWORK` (API 37), spelled out so older releases can name it too. */
    const val PERMISSION: String = "android.permission.ACCESS_LOCAL_NETWORK"

    val applies: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN

    fun granted(context: Context): Boolean =
        !applies || ContextCompat.checkSelfPermission(context, PERMISSION) == PackageManager.PERMISSION_GRANTED

    /**
     * True when [host] is, or resolves to, a private address. A lookup that fails
     * or times out answers false: the connection attempt that follows reports
     * the lookup failure with its own code.
     */
    suspend fun hostIsPrivate(host: String): Boolean {
        // The lookup blocks and cannot be interrupted, so it runs outside the caller's scope and is
        // simply abandoned when it takes too long.
        val lookup =
            lookups.async {
                try {
                    InetAddress.getAllByName(host).any(PrivateAddresses::isPrivate)
                } catch (_: UnknownHostException) {
                    false
                } catch (_: SecurityException) {
                    false
                }
            }
        return withTimeoutOrNull(LOOKUP_TIMEOUT_MS) { lookup.await() } ?: false
    }

    private val lookups = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private const val LOOKUP_TIMEOUT_MS = 5_000L
}
