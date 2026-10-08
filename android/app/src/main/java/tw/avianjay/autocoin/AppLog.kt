package tw.avianjay.autocoin

import android.content.Context
import android.util.Log
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** App 內的紀錄：最近幾百行留在記憶體給畫面顯示，全部寫進檔案。 */
object AppLog {
    private const val TAG = "autocoin"
    private val fmt = DateTimeFormatter.ofPattern("MM-dd HH:mm:ss")
    private val lines = ArrayDeque<String>()
    private var file: File? = null

    fun init(ctx: Context) {
        if (file == null) file = File(ctx.filesDir, "autocoin.log")
    }

    fun i(msg: String) = add("I", msg)
    fun w(msg: String) = add("W", msg)
    fun e(msg: String, t: Throwable? = null) = add("E", if (t == null) msg else "$msg\n${Log.getStackTraceString(t)}")
    fun d(msg: String) = add("D", msg, show = false)

    @Synchronized
    private fun add(level: String, msg: String, show: Boolean = true) {
        val line = "${LocalDateTime.now().format(fmt)} $level $msg"
        Log.i(TAG, line)
        if (show) {
            lines.addLast(line)
            while (lines.size > 300) lines.removeFirst()
        }
        file?.let { f ->
            try {
                if (f.length() > 2_000_000) f.renameTo(File(f.parentFile, "autocoin.old.log"))
                f.appendText(line + "\n")
            } catch (_: Exception) {
            }
        }
    }

    @Synchronized
    fun recent(n: Int = 200): List<String> = lines.toList().takeLast(n)
}
