package dev.msj.journal.core.net

import java.net.InetAddress
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class PrivateAddressesTest {
    private fun private(literal: String) = PrivateAddresses.isPrivate(InetAddress.getByName(literal))

    @Test
    fun privateRangesAreLocal() {
        listOf(
            "10.0.2.2",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.10",
            "100.64.0.1",
            "100.127.255.254",
            "169.254.10.10",
            "fd00::1",
            "fc12:3456::1",
            "fe80::1",
            "::ffff:10.1.2.3",
        ).forEach { assertTrue(private(it), it) }
    }

    @Test
    fun publicAndLoopbackAreNotLocal() {
        listOf(
            "8.8.8.8",
            "172.15.255.255",
            "172.32.0.1",
            "100.63.255.255",
            "100.128.0.1",
            "192.169.0.1",
            "127.0.0.1",
            "::1",
            "2001:4860:4860::8888",
            "::ffff:8.8.8.8",
        ).forEach { assertFalse(private(it), it) }
    }
}
