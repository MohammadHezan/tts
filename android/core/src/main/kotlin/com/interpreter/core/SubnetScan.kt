package com.interpreter.core

/**
 * The addresses worth probing when the phone looks for the interpreter's
 * computer on its Wi-Fi: every host on the phone's own subnet except itself.
 * Home networks are almost always /24; anything wider is narrowed to the /24
 * around the phone, so a scan never grows past 253 probes.
 */
object SubnetScan {

    fun candidateHosts(ipv4: String, prefixLength: Int): List<String> {
        val address = parse(ipv4) ?: return emptyList()
        val prefix = prefixLength.coerceIn(24, 30)
        val mask = (0xFFFFFFFFL shl (32 - prefix)) and 0xFFFFFFFFL
        val network = address and mask
        val broadcast = network or (mask.inv() and 0xFFFFFFFFL)
        return (network + 1 until broadcast)
            .filter { it != address }
            .map(::format)
    }

    private fun parse(ipv4: String): Long? {
        val parts = ipv4.trim().split('.')
        if (parts.size != 4) return null
        var value = 0L
        for (part in parts) {
            val octet = part.toIntOrNull()?.takeIf { it in 0..255 } ?: return null
            value = (value shl 8) or octet.toLong()
        }
        return value
    }

    private fun format(value: Long): String =
        listOf(24, 16, 8, 0).joinToString(".") { shift -> ((value shr shift) and 0xFF).toString() }
}
