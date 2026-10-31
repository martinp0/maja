package cz.martinpohl.maja

import java.net.URI

/** Adresa serveru bez cesty, query a hesla v URL. Sdílená s unit testem (čisté JVM). */
object Origins {
    fun normalize(input: String): String? {
        val trimmed = input.trim()
        if (trimmed.isEmpty()) return null
        val uri = try {
            URI(trimmed)
        } catch (_: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        if (uri.rawUserInfo != null) return null
        val host = uri.host ?: return null
        if (host.isBlank()) return null
        val path = uri.path ?: ""
        if (path.isNotEmpty() && path != "/") return null
        if (uri.query != null || uri.fragment != null) return null
        val port = uri.port
        val defaultPort = (scheme == "https" && port == 443) || (scheme == "http" && port == 80)
        val portPart = if (port == -1 || defaultPort) "" else ":$port"
        val hostPart = if (host.contains(":") && !host.startsWith("[")) "[$host]" else host
        return "$scheme://$hostPart$portPart"
    }
}
