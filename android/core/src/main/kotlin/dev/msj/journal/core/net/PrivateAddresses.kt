package dev.msj.journal.core.net

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress

/**
 * Classifies destination addresses that Android 17 may treat as "local
 * network" (and gate behind ACCESS_LOCAL_NETWORK for apps targeting API 37).
 * The platform does not document its ranges, so this errs on the side of
 * asking: RFC 1918, carrier-grade NAT 100.64.0.0/10 (used by mesh VPNs such as
 * NetBird), link-local, and IPv6 unique-local and link-local. Loopback is not
 * local network.
 */
public object PrivateAddresses {
    public fun isPrivate(address: InetAddress): Boolean =
        when (address) {
            is Inet4Address -> isPrivateV4(address.address)
            is Inet6Address -> isPrivateV6(address.address)
            else -> false
        }

    private fun isPrivateV4(bytes: ByteArray): Boolean {
        assert(bytes.size == 4) { "IPv4 address has 4 bytes" }
        val a = bytes[0].toInt() and 0xFF
        val b = bytes[1].toInt() and 0xFF
        return a == 10 ||
            (a == 172 && b in 16..31) ||
            (a == 192 && b == 168) ||
            (a == 100 && b in 64..127) ||
            (a == 169 && b == 254)
    }

    private fun isPrivateV6(bytes: ByteArray): Boolean {
        assert(bytes.size == 16) { "IPv6 address has 16 bytes" }
        val first = bytes[0].toInt() and 0xFF
        val second = bytes[1].toInt() and 0xFF
        val mapped = bytes.copyOfRange(0, 10).all { it.toInt() == 0 } && (bytes[10].toInt() and 0xFF) == 0xFF && (bytes[11].toInt() and 0xFF) == 0xFF
        return when {
            mapped -> isPrivateV4(bytes.copyOfRange(12, 16))
            first and 0xFE == 0xFC -> true
            first == 0xFE && second and 0xC0 == 0x80 -> true
            else -> false
        }
    }
}
