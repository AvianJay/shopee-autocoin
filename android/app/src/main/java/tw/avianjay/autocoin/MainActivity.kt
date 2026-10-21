package tw.avianjay.autocoin

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView

/** 主畫面：開無障礙設定、開始／停止、自動提領開關、今天的統計和紀錄。 */
class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var serviceState: TextView
    private lateinit var startStop: Button
    private var stopDisplayed = false
    private lateinit var summary: TextView
    private lateinit var logView: TextView

    private val tick = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLog.init(this)
        val settings = Settings(this)
        val pad = (16 * resources.displayMetrics.density).toInt()

        serviceState = TextView(this).apply { textSize = 15f }
        val openSettings = Button(this).apply {
            text = "開啟無障礙設定"
            setOnClickListener { startActivity(Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }
        startStop = Button(this).apply {
            setOnClickListener {
                val svc = CoinService.instance ?: return@setOnClickListener
                // 按鈕動作跟顯示的文字一致，避免狀態刷新前按「停止」反而重新開始。
                if (stopDisplayed) svc.stopBot() else svc.startBot()
                refresh()
            }
        }
        val withdraw = Switch(this).apply {
            text = "自動提領到「我的蝦幣」（每天最多 3 次）"
            isChecked = settings.withdraw
            setOnCheckedChangeListener { _, checked -> settings.withdraw = checked }
        }
        val hint = TextView(this).apply {
            textSize = 12f
            text = "離開蝦皮會暫停操作，切回蝦皮繼續。按「停止」後需再按「開始」才會執行。畫面最下面的小黑條也可以開始／停止。"
        }
        summary = TextView(this).apply {
            textSize = 13f
            setPadding(0, pad / 2, 0, pad / 2)
        }
        logView = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 11f
            setTextIsSelectable(true)
        }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(serviceState)
            addView(openSettings)
            addView(startStop)
            addView(withdraw)
            addView(hint)
            addView(summary)
            addView(ScrollView(this@MainActivity).apply { addView(logView) },
                LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        }
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        handler.post(tick)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(tick)
    }

    private fun refresh() {
        val svc = CoinService.instance
        serviceState.text = if (svc == null) "無障礙服務：未開啟（請在設定裡開啟「蝦幣助手」）"
        else "無障礙服務：已開啟｜${svc.statusText}"
        startStop.isEnabled = svc != null && !svc.stopping
        stopDisplayed = svc?.running == true || svc?.stopping == true
        startStop.text = when {
            svc?.stopping == true -> "停止中…"
            svc?.running == true -> "停止"
            else -> "開始"
        }
        summary.text = Store(this).summary().replace("｜", "\n")
        logView.text = AppLog.recent(150).asReversed().joinToString("\n")
    }
}
