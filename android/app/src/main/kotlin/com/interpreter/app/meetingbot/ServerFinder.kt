package com.interpreter.app.meetingbot

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.interpreter.core.SubnetScan
import java.net.Inet4Address
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Finds the computer running the interpreter (docker-compose.yml, port 8765)
 * on the phone's Wi-Fi, so nobody has to look up and type an IP address. It
 * asks every address on the phone's subnet for /healthz and takes the first
 * that answers as the interpreter ({"service": "interpreter"}).
 */
class ServerFinder(private val context: Context) {

    sealed interface Result {
        data class Found(val url: String) : Result
        data object NotFound : Result
        data object NoWifi : Result
    }

    private val probe = OkHttpClient.Builder()
        .connectTimeout(700, TimeUnit.MILLISECONDS)
        .readTimeout(2, TimeUnit.SECONDS)
        .callTimeout(3, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .build()

    suspend fun isInterpreter(serverUrl: String): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder().url(serverUrl.trimEnd('/') + "/healthz").build()
            probe.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                response.isSuccessful &&
                    Json.parseToJsonElement(body).jsonObject["service"]?.jsonPrimitive?.content == "interpreter"
            }
        }.getOrDefault(false)
    }

    suspend fun find(): Result = coroutineScope {
        val (ip, prefix) = wifiAddress() ?: return@coroutineScope Result.NoWifi
        val found = CompletableDeferred<String?>()
        val limit = Semaphore(PARALLEL_PROBES)
        val probes = SubnetScan.candidateHosts(ip, prefix).map { host ->
            launch {
                limit.withPermit {
                    val url = "http://$host:$PORT"
                    if (!found.isCompleted && isInterpreter(url)) found.complete(url)
                }
            }
        }
        launch {
            probes.joinAll()
            found.complete(null)
        }
        val url = found.await()
        probes.forEach { it.cancel() }
        if (url != null) Result.Found(url) else Result.NotFound
    }

    /** The phone's own IPv4 address and prefix on Wi-Fi (or Ethernet), if connected. */
    private fun wifiAddress(): Pair<String, Int>? {
        val connectivity = context.getSystemService(ConnectivityManager::class.java) ?: return null
        // Not just the default network: a Wi-Fi without internet can sit behind
        // mobile data as the default, and the computer is still on that Wi-Fi.
        @Suppress("DEPRECATION")
        val networks = listOfNotNull(connectivity.activeNetwork) + connectivity.allNetworks
        for (network in networks.distinct()) {
            val capabilities = connectivity.getNetworkCapabilities(network) ?: continue
            val local = capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            if (!local) continue
            val link = connectivity.getLinkProperties(network)?.linkAddresses
                ?.firstOrNull { it.address is Inet4Address && !it.address.isLoopbackAddress }
                ?: continue
            return link.address.hostAddress.orEmpty() to link.prefixLength
        }
        return null
    }

    companion object {
        const val PORT = 8765
        private const val PARALLEL_PROBES = 48
    }
}
