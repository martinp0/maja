package cz.martinpohl.maja

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OriginsTest {
    @Test
    fun stripsSlashAndDefaultPort() {
        assertEquals("https://tracker.example.com", Origins.normalize("https://tracker.example.com/"))
        assertEquals("https://tracker.example.com", Origins.normalize("https://tracker.example.com:443"))
    }

    @Test
    fun keepsTailscaleHttp() {
        assertEquals("http://100.1.2.3:8090", Origins.normalize("  http://100.1.2.3:8090 "))
    }

    @Test
    fun rejectsSecretsInTheUrlAndExtraPath() {
        assertNull(Origins.normalize("https://user:pass@tracker.example.com"))
        assertNull(Origins.normalize("https://tracker.example.com/maja"))
        assertNull(Origins.normalize("https://tracker.example.com/?x=1"))
        assertNull(Origins.normalize("ftp://tracker.example.com"))
        assertNull(Origins.normalize(""))
    }
}
