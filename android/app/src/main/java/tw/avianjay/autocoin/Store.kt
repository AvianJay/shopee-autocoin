package tw.avianjay.autocoin

import android.content.Context
import org.json.JSONObject
import java.time.LocalDate

/** 使用者可以調的設定。 */
class Settings(ctx: Context) {
    private val prefs = ctx.getSharedPreferences("settings", Context.MODE_PRIVATE)

    /** 自動提領（待提領 → 我的蝦幣）。 */
    var withdraw: Boolean
        get() = prefs.getBoolean("withdraw", true)
        set(v) = prefs.edit().putBoolean("withdraw", v).apply()

    val minRoomCoins = 0.5          // 卡片上「最多X」低於這個值就不考慮
    val explorePages = 6            // 「看更多」最多往下捲幾次
    val maxRoomMinutes = 75.0       // 保險：單一直播間最多待多久
    val noCoinGraceMs = 60_000L     // 「直播間蝦幣」不見多久就換台（領完、回到直播間時會暫時消失）
    val watchRoomMinutes = 10.5     // 只為觀看任務待的話，每間待多久（每場只算 10 分鐘）
    val panelEveryMinutes = 30.0    // 多久開一次面板，把觀看任務的蝦幣領進待提領
    val withdrawMin = 5.0           // 待提領至少這麼多才提領；收尾那次只要 ≥1
    val withdrawDailyLimit = 3      // 蝦皮規定每天最多提領 3 次
}

class RoomStat {
    var claims = 0
    var coins = 0.0
    var done = false
    var wheel = false      // 試過轉盤
    var wheelGot = false   // 轉盤結果已經記帳
    var watched = false
}

/** 當天的統計（對應電腦版 state.py 的 Day）。 */
class Day(val date: String) {
    var coinClaims = 0
    var coinTotal = 0.0
    var coinLimitHit = false
    var wheelSpins = 0
    var wheelTotal = 0.0
    var wheelDone = false
    var watchTotal = 0.0
    var watchMinutes = 0
    var watchDone = false
    var withdrawals = 0
    var withdrawn = 0.0
    var pending = 0.0
    var lastPanel = 0L
    var finalPanel = false
    val rooms = HashMap<String, RoomStat>()

    fun room(key: String) = rooms.getOrPut(key) { RoomStat() }
    fun roomDone(key: String) = key.isNotEmpty() && rooms[key]?.done == true
    fun flag(key: String, pick: (RoomStat) -> Boolean) = rooms[key]?.let(pick) ?: false

    fun toJson(): JSONObject = JSONObject().apply {
        put("date", date)
        put("coinClaims", coinClaims); put("coinTotal", coinTotal); put("coinLimitHit", coinLimitHit)
        put("wheelSpins", wheelSpins); put("wheelTotal", wheelTotal); put("wheelDone", wheelDone)
        put("watchTotal", watchTotal); put("watchMinutes", watchMinutes); put("watchDone", watchDone)
        put("withdrawals", withdrawals); put("withdrawn", withdrawn); put("pending", pending)
        put("lastPanel", lastPanel); put("finalPanel", finalPanel)
        put("rooms", JSONObject().apply {
            for ((k, r) in rooms) put(k, JSONObject().apply {
                put("claims", r.claims); put("coins", r.coins); put("done", r.done)
                put("wheel", r.wheel); put("wheelGot", r.wheelGot); put("watched", r.watched)
            })
        })
    }

    companion object {
        fun fromJson(o: JSONObject) = Day(o.getString("date")).apply {
            coinClaims = o.optInt("coinClaims"); coinTotal = o.optDouble("coinTotal", 0.0)
            coinLimitHit = o.optBoolean("coinLimitHit")
            wheelSpins = o.optInt("wheelSpins"); wheelTotal = o.optDouble("wheelTotal", 0.0)
            wheelDone = o.optBoolean("wheelDone")
            watchTotal = o.optDouble("watchTotal", 0.0); watchMinutes = o.optInt("watchMinutes")
            watchDone = o.optBoolean("watchDone")
            withdrawals = o.optInt("withdrawals"); withdrawn = o.optDouble("withdrawn", 0.0)
            pending = o.optDouble("pending", 0.0)
            lastPanel = o.optLong("lastPanel"); finalPanel = o.optBoolean("finalPanel")
            val rs = o.optJSONObject("rooms") ?: JSONObject()
            for (k in rs.keys()) {
                val r = rs.getJSONObject(k)
                rooms[k] = RoomStat().apply {
                    claims = r.optInt("claims"); coins = r.optDouble("coins", 0.0); done = r.optBoolean("done")
                    wheel = r.optBoolean("wheel"); wheelGot = r.optBoolean("wheelGot")
                    watched = r.optBoolean("watched")
                }
            }
        }
    }
}

class Store(ctx: Context) {
    private val prefs = ctx.getSharedPreferences("state", Context.MODE_PRIVATE)
    private var current: Day = load()

    private fun today() = LocalDate.now().toString()

    private fun load(): Day {
        val raw = prefs.getString("day", null)
        val day = try {
            raw?.let { Day.fromJson(JSONObject(it)) }
        } catch (e: Exception) {
            AppLog.w("讀取統計失敗，從頭開始：$e")
            null
        }
        return if (day != null && day.date == today()) day else Day(today())
    }

    /** 換日時自動重來。 */
    @get:Synchronized
    val day: Day
        get() {
            if (current.date != today()) {
                AppLog.i("換日了：${summary()}")
                current = Day(today())
                save()
            }
            return current
        }

    @Synchronized
    fun save() {
        prefs.edit().putString("day", current.toJson().toString()).apply()
    }

    fun summary(): String {
        val d = current
        return "${d.date} 直播間蝦幣 ${d.coinClaims} 次 +%.2f｜轉盤 ${d.wheelSpins} 次 +%.2f｜觀看任務 ${d.watchMinutes} 分鐘 +%.2f｜提領 ${d.withdrawals}/3 次 %.0f｜待提領 %.2f"
            .format(d.coinTotal, d.wheelTotal, d.watchTotal, d.withdrawn, d.pending)
    }
}
