package com.interpreter.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SubnetScanTest {
    @Test
    fun `a home 24 network yields every other host`() {
        val hosts = SubnetScan.candidateHosts("192.168.1.23", 24)
        assertEquals(253, hosts.size) // .1-.254 minus the phone itself
        assertEquals("192.168.1.1", hosts.first())
        assertEquals("192.168.1.254", hosts.last())
        assertFalse("192.168.1.23" in hosts)
        assertFalse("192.168.1.0" in hosts)
        assertFalse("192.168.1.255" in hosts)
    }

    @Test
    fun `wider networks are narrowed to the phone's 24`() {
        val hosts = SubnetScan.candidateHosts("10.20.30.40", 16)
        assertEquals(253, hosts.size)
        assertTrue(hosts.all { it.startsWith("10.20.30.") })
    }

    @Test
    fun `narrow networks stay narrow`() {
        // /29: network .8, broadcast .15, hosts .9-.14, minus the phone
        assertEquals(
            listOf("172.16.5.9", "172.16.5.10", "172.16.5.12", "172.16.5.13", "172.16.5.14"),
            SubnetScan.candidateHosts("172.16.5.11", 29),
        )
    }

    @Test
    fun `garbage in, nothing out`() {
        assertTrue(SubnetScan.candidateHosts("not an ip", 24).isEmpty())
        assertTrue(SubnetScan.candidateHosts("300.1.1.1", 24).isEmpty())
    }
}
