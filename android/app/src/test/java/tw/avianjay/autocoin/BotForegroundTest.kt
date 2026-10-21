package tw.avianjay.autocoin

import android.app.Application
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class BotForegroundTest {
    private val app: Application get() = RuntimeEnvironment.getApplication()

    @Test
    fun leavingShopeeWaitsWithoutLaunchingOrPressingBack() {
        for (pkg in listOf("com.android.launcher", app.packageName, "android", "com.android.intentresolver", "")) {
            val phone = FakePhone(pkg)
            val statuses = ArrayList<String>()
            runBot(phone) {
                statuses.add(it)
                phone.cancelled = true
            }

            assertEquals("foreground: $pkg", listOf("已暫停，請切回蝦皮繼續"), statuses)
            assertTrue(phone.launches.isEmpty())
            assertEquals(0, phone.actions)
        }
    }

    @Test
    fun explicitStartCanOpenShopeeOnce() {
        val phone = FakePhone("com.android.launcher", initiallyInShopee = false)
        phone.cancelOnLaunch = true

        runBot(phone) { throw AssertionError("Unexpected status: $it") }

        assertEquals(listOf(true), phone.launches)
        assertEquals(0, phone.actions)
    }

    @Test
    fun switchingAppsBetweenSnapshotAndActionReturnsToWaiting() {
        val phone = FakePhone(SHOPEE)
        phone.switchOnSnapshot = true
        val statuses = ArrayList<String>()

        runBot(phone) {
            statuses.add(it)
            phone.cancelled = true
        }

        assertEquals(listOf("已暫停，請切回蝦皮繼續"), statuses)
        assertTrue(phone.launches.isEmpty())
        assertEquals(0, phone.actions)
        assertFalse(AppLog.recent().any { "出錯了" in it })
    }

    private fun runBot(phone: FakePhone, status: (String) -> Unit) {
        AppLog.init(app)
        Bot(phone, Store(app), Settings(app), status).run()
    }

    private class FakePhone(
        private var pkg: String,
        private val initiallyInShopee: Boolean = true,
    ) : Phone {
        override val width = 1080
        override val height = 2400
        var cancelled = false
        var cancelOnLaunch = false
        var switchOnSnapshot = false
        var actions = 0
        val launches = ArrayList<Boolean>()
        private var firstForeground = true

        override fun checkActive() {
            if (cancelled) throw InterruptedException()
        }

        override fun foreground(): String {
            if (firstForeground) {
                firstForeground = false
                if (initiallyInShopee) return SHOPEE
            }
            return pkg
        }

        override fun snapshot(): Snap {
            if (switchOnSnapshot) {
                pkg = "com.android.launcher"
                throw AppInBackground()
            }
            throw AssertionError("Must wait before reading Shopee")
        }

        override fun launchShopee(initial: Boolean) {
            launches.add(initial)
            if (cancelOnLaunch) cancelled = true
        }

        override fun tap(p: Pt) { actions++ }
        override fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, ms: Long) { actions++ }
        override fun back() { actions++ }
        override fun screenOn() = true
        override fun locked() = false
        override fun lastToast() = ""
        override fun clearToast() {}
        override fun save(name: String, content: String) {}
    }
}
