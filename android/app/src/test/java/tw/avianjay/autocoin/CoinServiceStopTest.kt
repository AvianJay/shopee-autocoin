package tw.avianjay.autocoin

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.ApplicationInfo
import android.content.pm.ResolveInfo
import android.os.Looper
import android.os.PowerManager
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.annotation.LooperMode
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowAccessibilityService
import org.robolectric.util.ReflectionHelpers
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
@LooperMode(LooperMode.Mode.PAUSED)
class CoinServiceStopTest {
    private lateinit var service: CoinService

    @Before
    fun setUp() {
        service = Robolectric.buildService(CoinService::class.java).create().get()
        shadowOf(service.getSystemService(PowerManager::class.java)).setIsInteractive(true)

        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setPackage(SHOPEE)
        val activity = ActivityInfo().apply {
            packageName = SHOPEE
            name = "$SHOPEE.MainActivity"
            applicationInfo = ApplicationInfo().apply { packageName = SHOPEE }
        }
        shadowOf(service.packageManager).addResolveInfoForIntent(launcher, ResolveInfo().apply {
            activityInfo = activity
        })
        assertNotNull(service.packageManager.getLaunchIntentForPackage(SHOPEE))
    }

    @After
    fun tearDown() {
        service.stopBot()
        worker()?.join(5000)
        shadowOf(Looper.getMainLooper()).idle()
        service.onDestroy()
    }

    @Test
    fun stopCancelsQueuedLaunchAndPendingStatusUpdates() {
        service.startBot()
        val thread = waitForQueuedLaunch()

        service.stopBot()
        thread.join(5000)
        assertFalse(thread.isAlive)
        shadowOf(Looper.getMainLooper()).idle()

        assertNull(shadowOf(service as AccessibilityService).nextStartedActivity)
        assertFalse(service.running)
        assertFalse(service.stopping)
        assertEquals("已停止", service.statusText)
    }

    @Test
    fun repeatedStartWhileStoppingCannotCreateAnotherWorker() {
        service.startBot()
        val thread = waitForQueuedLaunch()

        service.stopBot()
        service.startBot()

        assertSame(thread, worker())
        assertFalse(service.running)
        assertTrue(service.stopping)
        thread.join(5000)
        shadowOf(Looper.getMainLooper()).idle()
        assertNull(shadowOf(service as AccessibilityService).nextStartedActivity)
        assertEquals("已停止", service.statusText)
    }

    @Test
    fun serviceInterruptionAlsoStopsPendingAutomation() {
        service.startBot()
        val thread = waitForQueuedLaunch()

        service.onInterrupt()
        thread.join(5000)
        shadowOf(Looper.getMainLooper()).idle()

        assertNull(shadowOf(service as AccessibilityService).nextStartedActivity)
        assertFalse(service.running)
        assertEquals("已停止", service.statusText)
    }

    @Test
    fun stopCancelsQueuedTap() {
        setForeground(SHOPEE, "tab_bar_button_video_and_live")
        service.startBot()
        val thread = waitForQueuedAction("tap")

        service.stopBot()
        thread.join(5000)
        shadowOf(Looper.getMainLooper()).idle()

        assertTrue(shadowOf(service as AccessibilityService).gesturesDispatched.isEmpty())
        assertEquals("已停止", service.statusText)
    }

    @Test
    fun goingHomeBeforeQueuedBackPreventsOperatingTheLauncher() {
        setForeground(SHOPEE)
        service.startBot()
        val thread = waitForQueuedAction("back")

        setForeground("com.android.launcher")
        shadowOf(Looper.getMainLooper()).idle()
        val deadline = System.nanoTime() + 5_000_000_000L
        while (thread.state != Thread.State.TIMED_WAITING && System.nanoTime() < deadline) {
            Thread.sleep(1)
        }
        shadowOf(Looper.getMainLooper()).idle()

        val shadow = shadowOf(service as AccessibilityService)
        assertTrue(shadow.globalActionsPerformed.isEmpty())
        assertTrue(shadow.gesturesDispatched.isEmpty())
        assertNull(shadow.nextStartedActivity)
        assertEquals("已暫停，請切回蝦皮繼續", service.statusText)
    }

    @Test
    fun clickingAStopButtonBeforeItsNextRefreshCannotRestartTheBot() {
        ReflectionHelpers.setStaticField(CoinService::class.java, "instance", service)
        val controller = Robolectric.buildActivity(MainActivity::class.java).create()
        val activity = controller.get()
        service.startBot()
        val thread = waitForQueuedLaunch()
        ReflectionHelpers.callInstanceMethod<Unit>(activity, "refresh")
        val button = ReflectionHelpers.getField<Button>(activity, "startStop")
        assertEquals("停止", button.text.toString())

        service.stopBot()
        thread.join(5000)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals("停止", button.text.toString())
        button.performClick()

        assertFalse(service.running)
        assertNull(worker())
        assertNull(shadowOf(service as AccessibilityService).nextStartedActivity)
        controller.destroy()
    }

    @Test
    @Config(shadows = [InterruptConsumingAccessibilityShadow::class])
    fun stopStillWorksWhenAndroidConsumesTheThreadInterrupt() {
        setForeground(SHOPEE)
        val shadow = Shadow.extract<InterruptConsumingAccessibilityShadow>(service)
        service.startBot()
        val thread = checkNotNull(worker())
        assertTrue("Bot should reach the blocking UI read", shadow.readStarted.await(5, TimeUnit.SECONDS))

        service.stopBot()
        thread.join(5000)
        assertFalse("Bot must exit even after the interrupt flag is cleared", thread.isAlive)
        shadowOf(Looper.getMainLooper()).idle()

        assertTrue(shadow.interruptConsumed)
        assertTrue(shadow.globalActionsPerformed.isEmpty())
        assertTrue(shadow.gesturesDispatched.isEmpty())
        assertNull(shadow.nextStartedActivity)
        assertEquals("已停止", service.statusText)
    }

    private fun setForeground(pkg: String, desc: String = "") {
        val root = AccessibilityNodeInfo.obtain().apply {
            packageName = pkg
            contentDescription = desc
            isVisibleToUser = true
        }
        shadowOf(service as AccessibilityService).setRootInActiveWindow(root)
        assertEquals(pkg, service.foreground())
    }

    private fun worker(): Thread? = ReflectionHelpers.getField(service, "worker")

    private fun waitForQueuedLaunch() = waitForQueuedAction("launchShopee")

    private fun waitForQueuedAction(method: String): Thread {
        val thread = checkNotNull(worker())
        val deadline = System.nanoTime() + 5_000_000_000L
        fun queued(): Boolean {
            val frames = thread.stackTrace.filter { it.className == CoinService::class.java.name }
            return thread.state == Thread.State.WAITING &&
                frames.any { it.methodName == "botAction" } && frames.any { it.methodName == method }
        }
        while (!queued() && System.nanoTime() < deadline) {
            Thread.sleep(1)
        }
        assertTrue("Bot should be waiting for queued $method: ${thread.stackTrace.toList()}", queued())
        return thread
    }
}

/** 模擬 Android 在等待 UI 回應時吃掉 InterruptedException 的情況。 */
@Implements(AccessibilityService::class)
class InterruptConsumingAccessibilityShadow : ShadowAccessibilityService() {
    val readStarted = CountDownLatch(1)
    @Volatile var interruptConsumed = false
    private var reads = 0

    @Implementation
    override fun getRootInActiveWindow(): AccessibilityNodeInfo? {
        if (Thread.currentThread().name == "autocoin" && ++reads == 2) {
            readStarted.countDown()
            try {
                CountDownLatch(1).await(5, TimeUnit.SECONDS)
            } catch (_: InterruptedException) {
                interruptConsumed = true
            }
        }
        return super.getRootInActiveWindow()
    }
}
