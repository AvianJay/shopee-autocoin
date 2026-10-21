package tw.avianjay.autocoin

import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

// 主流程（對應電腦版 bot.py）：挑有蝦幣的直播間、待著領取、轉盤、定時開面板領觀看任務與提領。

/** Bot 需要手機提供的操作，由無障礙服務實作。 */
interface Phone {
    val width: Int
    val height: Int
    fun checkActive()
    fun snapshot(): Snap
    fun tap(p: Pt)
    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, ms: Long = 400)
    fun back()
    fun foreground(): String
    fun screenOn(): Boolean
    fun locked(): Boolean
    fun launchShopee(initial: Boolean = false)
    fun lastToast(): String
    fun clearToast()
    fun save(name: String, content: String)
}

class StopBot(msg: String) : Exception(msg)
class AppInBackground : Exception()

// App 內的提示文字（取自 APK 的 zh-rTW 字串）
private val MSG_RISK = listOf("不符合領取此獎勵資格", "偵測到風險活動", "您已達多裝置使用上限", "已達裝置數限制")
private val MSG_DAILY_LIMIT = listOf("已超過今天領取的次數限制", "您已達領取上限")
private val MSG_ROOM_DONE = listOf("直播蝦幣已全部發放")
private val MSG_WHEEL_LIMIT = listOf("達到每日參與上限", "達到每日上限")
private val CONFIRM_TEXTS = setOf("確認", "確定", "確認提領")
// 觀眾、直播主可以自由打字的地方，比對提示文字時要略過
private val FREE_TEXT_IDS = setOf("public_screen_view", "danmaku_view", "scr_pin", "tv_live_title", "tv_anchor_name")

private val REASONS = mapOf(
    "no_coins" to "沒有直播間蝦幣",
    "room_done" to "這場的蝦幣發完了",
    "daily_limit" to "今天的直播間蝦幣次數用完了",
    "timeout" to "待太久了",
    "watched" to "觀看任務在這間已累計 10 分鐘",
    "wheel_checked" to "轉盤處理完了",
    "lost" to "畫面跑掉了",
    "switched" to "直播間被換掉了",
)
// 因為這些原因離開的直播間，今天就不再進去
private val DONE_REASONS = setOf("no_coins", "room_done", "timeout", "watched", "wheel_checked")
private val STAMP = DateTimeFormatter.ofPattern("MMdd-HHmmss")

private fun round2(v: Double) = (v * 100).roundToInt() / 100.0

class Bot(
    private val phone: Phone,
    private val store: Store,
    private val cfg: Settings,
    private val status: (String) -> Unit,
) {
    private val day get() = store.day
    private val rainSeen = HashSet<String>()

    // 原本在 1080x2400 上量的座標，換算成目前螢幕
    private fun x(v: Int) = v * phone.width / 1080
    private fun y(v: Int) = v * phone.height / 2400

    // ---------------- 基本工具 ----------------
    private fun now() = System.currentTimeMillis()
    private fun sleep(ms: Long) {
        phone.checkActive()
        Thread.sleep(Jitter.ms(ms))
        phone.checkActive()
    }

    // 每個直播間用不同的上限，避免「每次都剛好待 10.5 分鐘」這種固定週期指紋
    private val roomCaps = HashMap<String, Double>()
    private fun roomCap(key: String, base: Double, pct: Double = 0.25): Double =
        roomCaps.getOrPut("$key@$base") { Jitter.up(base, pct) }

    private fun look(): Pair<Screen, Snap> {
        val s = phone.snapshot()
        return detect(s) to s
    }

    private fun waitFor(wanted: Set<Screen>, timeoutMs: Long = 10_000): Pair<Screen, Snap> {
        val deadline = now() + timeoutMs
        while (true) {
            val cur = look()
            if (cur.first in wanted || now() >= deadline) return cur
            sleep(700)
        }
    }

    /** 存下目前畫面的 UI 樹（文字版），之後分析沒看過的狀態用。 */
    private fun capture(tag: String, s: Snap?) {
        try {
            val snap = s ?: phone.snapshot()
            phone.save("${LocalDateTime.now().format(STAMP)}-${tag.replace(Regex("""[^\w.-]+"""), "_")}.txt", snap.dump())
        } catch (e: InterruptedException) {
            throw e
        } catch (e: Exception) {
            AppLog.d("存畫面失敗：$e")
        }
    }

    /** 比對 App 的提示文字。遇到風控就停止；回傳 daily_limit / room_done / 空字串。 */
    private fun checkMessages(s: Snap, toast: String = ""): String {
        val texts = s.textsExcluding(FREE_TEXT_IDS) + listOf(toast).filter { it.isNotEmpty() }
        fun hit(keys: List<String>) = texts.firstOrNull { t -> keys.any { it in t } }
        hit(MSG_RISK)?.let {
            capture("risk", s)
            throw StopBot("蝦皮回報「$it」，為了帳號安全先停止")
        }
        hit(MSG_DAILY_LIMIT)?.let {
            if (!day.coinLimitHit) {
                AppLog.i("直播間蝦幣今天的次數用完了（$it）")
                day.coinLimitHit = true
                store.save()
            }
            return "daily_limit"
        }
        return if (hit(MSG_ROOM_DONE) != null) "room_done" else ""
    }

    // ---------------- 等使用者回到蝦皮，不搶走其他 App 的前景 ----------------
    private fun ensureApp() {
        var waiting = ""
        while (true) {
            phone.checkActive()
            if (!phone.screenOn() || phone.locked()) {
                if (waiting != "screen") {
                    AppLog.w("螢幕關著或鎖定中，等解鎖…")
                    waiting = "screen"
                }
                status("等待解鎖")
                sleep(15_000)
                continue
            }
            val pkg = phone.foreground()
            if (pkg == SHOPEE) {
                if (waiting.isNotEmpty()) AppLog.i("蝦皮回到前景，繼續")
                return
            }
            if (waiting != pkg) {
                AppLog.i("前景是 $pkg，可能有人在用手機，等它結束…")
                waiting = pkg
            }
            status("已暫停，請切回蝦皮繼續")
            sleep(2000)
        }
    }

    // ---------------- 導航 ----------------
    private fun closeOverlay(s: Snap) {
        val btn = s.id("ic_close")
        if (btn != null) phone.tap(btn.center) else phone.back()
    }

    private fun closeExplore(s: Snap) {
        val btn = s.id("collapse_icon")
        if (btn != null) phone.tap(btn.center) else phone.back()
    }

    /** 不管現在在哪，回到某個直播間。 */
    private fun goLive(): Snap {
        repeat(2) {
            var last: Snap? = null
            for (i in 0 until 10) {
                val (scr, s) = look()
                last = s
                if (scr == Screen.ROOM) return s
                AppLog.d("goLive：目前在${scr.label}")
                when (scr) {
                    Screen.LIVE_PREVIEW -> phone.swipe(x(540), y(1800), x(540), y(600), 300)
                    Screen.VIDEO_TAB -> s.desc("click 1 tab")?.let { phone.tap(it.center) }
                    Screen.HOME -> s.desc("tab_bar_button_video_and_live")?.let { phone.tap(it.center) }
                    Screen.EXPLORE -> closeExplore(s)
                    Screen.WHEEL, Screen.WHEEL_RESULT -> closeOverlay(s)
                    Screen.FOLLOW_DIALOG -> s.id("tv_cancel")?.let { phone.tap(it.center) }
                    Screen.OTHER_APP -> ensureApp()
                    else -> phone.back()
                }
                sleep(2500)
            }
            AppLog.w("一直回不到直播間，重新打開蝦皮")
            capture("lost", last)
            phone.launchShopee()
            sleep(10_000)
            ensureApp()
        }
        throw IllegalStateException("回不到直播間")
    }

    /** 收掉蓋在直播間上面的東西（轉盤、面板、提領頁…）。回不到直播間就回傳 false。 */
    private fun backToRoom(scr0: Screen, s0: Snap): Boolean {
        var scr = scr0
        var s = s0
        repeat(4) {
            when (scr) {
                Screen.ROOM -> return true
                Screen.WHEEL, Screen.WHEEL_RESULT -> closeOverlay(s)
                Screen.FOLLOW_DIALOG -> s.id("tv_cancel")?.let { phone.tap(it.center) }
                Screen.PANEL, Screen.WITHDRAW, Screen.WEB, Screen.UNKNOWN -> {
                    if (scr == Screen.UNKNOWN) capture("unknown", s)
                    phone.back()
                }
                Screen.OTHER_APP -> ensureApp()
                else -> return false
            }
            sleep(2000)
            val cur = look()
            scr = cur.first
            s = cur.second
        }
        return scr == Screen.ROOM
    }

    /** 打開直播間右上角的「看更多」列表。 */
    private fun openExplore(): Snap? {
        repeat(3) {
            var (scr, s) = look()
            if (scr != Screen.EXPLORE) {
                if (scr != Screen.ROOM) s = goLive()
                val r = readRoom(s)
                when {
                    r.explore != null -> phone.tap(r.explore)
                    r.close != null -> {  // 沒有「看更多」的獨立直播間，先關掉
                        phone.tap(r.close)
                        sleep(2500)
                        return@repeat
                    }
                    else -> {
                        AppLog.w("直播間沒有「看更多」")
                        capture("no-explore", s)
                        return null
                    }
                }
                val cur = waitFor(setOf(Screen.EXPLORE), 8000)
                if (cur.first != Screen.EXPLORE) return@repeat
                sleep(2000)  // 廣告彈窗通常晚一點才出來
                s = phone.snapshot()
            }
            if (dismissPopup(s)) {
                sleep(1000)
                s = phone.snapshot()
            }
            return s
        }
        return null
    }

    /** 「看更多」列表上偶爾蓋著廣告：一張大圖＋下面一個小關閉鈕，兩者都沒有文字或 ID。 */
    private fun dismissPopup(s: Snap): Boolean {
        val bare = s.nodes.filter { it.clickable && it.rid.isEmpty() && it.text.isEmpty() && it.desc.isEmpty() }
        val big = bare.firstOrNull { it.width > x(600) && it.height > y(700) } ?: return false
        val close = bare.firstOrNull {
            it.width in x(60)..x(150) && it.height in x(60)..x(150) && it.bounds.top >= big.bounds.bottom - 30
        } ?: return false
        AppLog.i("關掉廣告彈窗")
        phone.tap(close.center)
        return true
    }

    private fun scrollTop(s0: Snap): Snap {
        var s = s0
        repeat(cfg.explorePages + 3) {
            if (readCards(s).any { it.rid == "live_card_cover_0" }) return s
            phone.swipe(x(590), y(700), x(590), y(2000), 300)
            sleep(800)
            s = phone.snapshot()
        }
        return s
    }

    /** 往下捲「看更多」列表，收集卡片（rid → Card）。 */
    private fun scan(s0: Snap): Map<String, Card> {
        var s = s0
        val cards = LinkedHashMap<String, Card>()
        for (page in 0..cfg.explorePages) {
            var new = 0
            for (c in readCards(s)) {
                if (c.key.isEmpty()) continue
                val old = cards[c.rid]
                if (old == null) new++
                // 卡片露出越多資訊越完整（標題、直播主）
                if (old == null || c.height > old.height) cards[c.rid] = c
            }
            if (page == cfg.explorePages || (page > 0 && new == 0)) break
            phone.swipe(x(590), y(1900), x(590), y(700), 500)
            sleep(1200)
            s = phone.snapshot()
        }
        return cards
    }

    /** 挑下一間：還能轉盤就先去「抽獎」的；再來挑「最多X」最高的；都沒有就去累積觀看任務。 */
    private fun choose(cards: Map<String, Card>): Pair<Card?, String> {
        val day = this.day
        val fresh = cards.values.filter { !day.roomDone(it.key) }
        if (!day.wheelDone) {
            fresh.firstOrNull { it.lottery && !day.flag(it.key) { r -> r.wheel } }?.let {
                return it to (if (day.coinLimitHit) "wheel" else "coins")
            }
        }
        if (!day.coinLimitHit) {
            fresh.filter { (it.maxCoins ?: 0.0) >= cfg.minRoomCoins }.maxByOrNull { it.maxCoins ?: 0.0 }?.let {
                return it to "coins"
            }
        }
        if (!day.watchDone) {
            fresh.firstOrNull { !day.flag(it.key) { r -> r.watched } }?.let { return it to "watch" }
        }
        return null to ""
    }

    /** 捲回那張卡片並點進去。 */
    private fun enter(card: Card): Snap? {
        repeat(cfg.explorePages * 2 + 2) {
            val s = phone.snapshot()
            val hit = readCards(s).firstOrNull { it.rid == card.rid && it.height >= y(300) }
            if (hit != null) {
                phone.tap(hit.center)
                val (scr, room) = waitFor(setOf(Screen.ROOM), 15_000)
                return if (scr == Screen.ROOM) room else null
            }
            phone.swipe(x(590), y(800), x(590), y(1700), 500)  // 往回捲
            sleep(1000)
        }
        AppLog.w("列表裡找不到 ${card.key}")
        return null
    }

    private fun pick(): Pair<Card?, String> {
        status("找直播間中")
        val s = openExplore() ?: return null to ""
        val cards = scan(scrollTop(s))
        val (card, purpose) = choose(cards)
        if (card == null) {
            closeExplore(phone.snapshot())
            return null to ""
        }
        val badge = card.maxCoins?.let { "最多 ${"%.2f".format(it).trimEnd('0').trimEnd('.')}" }
            ?: if (card.lottery) "抽獎" else "-"
        AppLog.i("→ 進入 ${card.key}（$badge，${card.viewers} 人在看）目的：$purpose")
        if (enter(card) == null) return null to ""
        return card to purpose
    }

    /** 離開目前的直播間。獨立直播間按 X；首頁直播分頁裡那間不用關，下次直接開「看更多」。 */
    private fun leave() {
        val (scr, s) = look()
        if (scr == Screen.ROOM) {
            readRoom(s).close?.let {
                phone.tap(it)
                sleep(2500)
            }
        }
    }

    // ---------------- 待在直播間 ----------------
    /** 待在目前的直播間直到該走了。purpose：coins / wheel / watch。回傳（原因, 直播主）。 */
    private fun stay(purpose: String): Pair<String, String> {
        val start = now()
        var coinMissing = 0L
        var oddState = 0L
        var watchStuck = 0L
        var wheelTries = 0
        var wheelSettled = false  // 這間的轉盤已經看到結果了
        var lastWheel = ""
        var key = ""
        while (true) {
            val (scr, s) = look()
            if (scr != Screen.ROOM) {
                if (!backToRoom(scr, s)) return "lost" to key
                continue
            }
            val r = readRoom(s)
            if (key.isNotEmpty() && r.key.isNotEmpty() && r.key != key) return "switched" to key
            if (key.isEmpty()) {
                AppLog.d("進房：${r.key}｜直播間蝦幣 ${r.coin.coin} ${r.coin.state.ifEmpty { "-" }}｜轉盤 '${r.wheel}'｜紅包雨 ${r.rains.ifEmpty { "-" }}")
            } else if (r.wheel != lastWheel) {
                AppLog.d("轉盤狀態：'$lastWheel' → '${r.wheel}'")
            }
            lastWheel = r.wheel
            if (key.isEmpty()) key = r.key
            val day = this.day

            val verdict = checkMessages(s)
            if (verdict == "room_done" || (verdict == "daily_limit" && purpose == "coins")) return verdict to key

            // 幸運轉盤：每間一次，每天 10 次。「已參加」只代表關注過，不代表轉過
            if (r.wheel in setOf("參加", "已參加") && !wheelSettled && !day.wheelDone && wheelTries < 2) {
                wheelTries++
                wheelSettled = playWheel(r)
                continue
            }
            if (purpose == "wheel") return "wheel_checked" to key

            if (r.rains.isNotEmpty()) observeRain(r, s)

            // 觀看任務到點會自己領，卡在「領取」太久才幫忙按
            if (r.watch.claimable) {
                if (watchStuck == 0L) watchStuck = now()
                if (now() - watchStuck > 15_000) {
                    r.watch.button?.let { phone.tap(it) }
                    watchStuck = 0L
                }
            } else {
                watchStuck = 0L
            }
            if (r.watch.state == "明天再來" && !day.watchDone) {
                AppLog.i("觀看任務今天做完了")
                day.watchDone = true
                store.save()
            }

            if (panelDue()) {
                panelTasks()
                continue
            }

            val elapsed = now() - start
            if (elapsed > roomCap(key, cfg.maxRoomMinutes) * 60_000) return "timeout" to key
            if (purpose == "watch") {
                if (elapsed > roomCap(key, cfg.watchRoomMinutes) * 60_000) return "watched" to key
                status("累積觀看任務｜${key.take(12)}")
                sleep(20_000)
                continue
            }

            // 直播間蝦幣
            if (!r.coin.present) {
                if (coinMissing == 0L) coinMissing = now()
                if (now() - coinMissing > cfg.noCoinGraceMs) return "no_coins" to key
                sleep(3000)
                continue
            }
            coinMissing = 0L
            if (r.coin.claimable) {
                if ("成功" in r.claimed) sleep(1000)  // 上一次的成功提示還在，按鈕還沒換掉
                else claim(r)
                continue
            }
            val secs = r.coin.seconds
            if (secs == null) {
                if (oddState == 0L) oddState = now()
                if (now() - oddState > 30_000) {
                    capture("coin-${r.coin.state}", s)
                    return "coin_state:${r.coin.state}" to key
                }
                sleep(3000)
                continue
            }
            oddState = 0L
            status("${key.take(12)}｜${r.coin.state} 後領 ${r.coin.coin ?: "?"}｜今天 ${day.coinClaims} 次 +%.2f".format(day.coinTotal))
            var nap = secs - Jitter.int(1, 6)  // 不要每次都剛好提前 2 秒醒
            for (state in r.rains.values) countdown(state)?.let { nap = minOf(nap, it - 2) }  // 紅包雨快開始時要醒著
            sleep(nap.coerceIn(1, 20) * 1000L)
        }
    }

    /** 按「直播間蝦幣」的領取，確認有成功。 */
    private fun claim(r: Room) {
        val key = r.key
        val before = r.coin.coin ?: 0.0
        phone.clearToast()
        r.coin.button?.let { phone.tap(it) }
        var amount: Double? = null
        val deadline = now() + 8000
        while (now() < deadline) {
            val s = phone.snapshot()
            val got = readRoom(s).claimed
            if ("成功" in got) {
                amount = num(got) ?: before
                break
            }
            val verdict = checkMessages(s, phone.lastToast())
            if (verdict.isNotEmpty()) {
                AppLog.i("領取沒成功：${REASONS[verdict] ?: verdict}")
                return
            }
            sleep(300)
        }
        if (amount == null) {
            // 沒看到成功提示：倒數已經重新開始的話也算成功
            val s = phone.snapshot()
            val after = readRoom(s).coin
            if ((after.seconds ?: 0) > 120) {
                amount = before
            } else {
                AppLog.w("直播間蝦幣按了沒反應（現在顯示：${after.state.ifEmpty { "不見了" }}）")
                capture("claim-unknown", s)
                return
            }
        }
        val day = this.day
        val room = day.room(key)
        room.claims++
        room.coins = round2(room.coins + amount)
        day.coinClaims++
        day.coinTotal = round2(day.coinTotal + amount)
        store.save()
        AppLog.i("✔ 直播間蝦幣 +%.2f｜%s 第 %d 次｜今天 %d 次，共 %.2f".format(amount, key, room.claims, day.coinClaims, day.coinTotal))
        // 成功提示出來時按鈕常常還停在「領取」；等它換掉再走，免得又按一次
        //（那個位置按下去會打開任務獎勵面板）
        for (i in 0 until 10) {
            sleep(800)
            if (!readRoom(phone.snapshot()).coin.claimable) break
        }
    }

    /** 轉這間的幸運轉盤。看到結果（這次轉的或之前轉過的）就回傳 true。 */
    private fun playWheel(r: Room): Boolean {
        val key = r.key
        day.room(key).wheel = true
        store.save()
        r.wheelButton?.let { phone.tap(it) }
        var cur = waitFor(setOf(Screen.WHEEL, Screen.WHEEL_RESULT), 6000)
        var spins = 0
        for (i in 0 until 6) {
            val (scr, s) = cur
            if (scr == Screen.WHEEL_RESULT) {
                wheelResult(s, key)
                return true
            }
            if (scr == Screen.FOLLOW_DIALOG) {
                AppLog.i("轉盤限粉絲，關注 $key")
                s.id("tv_confirm")?.let { phone.tap(it.center) }
                // 對話框關掉後它的空殼視窗會擋一陣子，期間讀不到底下的轉盤（轉盤其實還開著）
                cur = waitFor(setOf(Screen.WHEEL, Screen.WHEEL_RESULT), 8000)
                if (cur.first == Screen.UNKNOWN) cur = Screen.WHEEL to cur.second
                continue
            }
            if (scr != Screen.WHEEL || spins >= 3) break
            if (s.textsExcluding(emptySet()).any { t -> MSG_WHEEL_LIMIT.any { it in t } }) {
                wheelExhausted()
                break
            }
            // 「開始」沒有獨立節點，按轉盤正中央
            phone.tap(s.id("iv_draw_light")?.center ?: Pt(x(540), y(1100)))
            spins++
            cur = waitFor(setOf(Screen.WHEEL_RESULT, Screen.FOLLOW_DIALOG), 12_000)
        }
        AppLog.i("轉盤沒有結果（可能還沒開始或已結束）")
        capture("wheel-unknown", cur.second)
        if (cur.first != Screen.ROOM) closeOverlay(cur.second)
        return false
    }

    private fun wheelResult(s: Snap, key: String) {
        val text = s.id("tv_result")?.text ?: ""
        val amount = if ("獲得" in text) num(text) else null
        val left = num(s.id("tv_more")?.text)
        val day = this.day
        val room = day.room(key)
        // 每間只能轉一次，第一次看到結果就記帳（按「關注」時可能就直接轉了，當下讀不到結果）
        if (amount != null && !room.wheelGot) {
            room.wheelGot = true
            day.wheelSpins++
            day.wheelTotal = round2(day.wheelTotal + amount)
            AppLog.i("✔ 轉盤 +%.2f｜%s｜今天還能轉 %s 次".format(amount, key, left?.toInt()?.toString() ?: "?"))
        } else if (text.isNotEmpty()) {
            AppLog.i("轉盤：$text")
        }
        if (left == 0.0 || MSG_WHEEL_LIMIT.any { it in text }) wheelExhausted()
        store.save()
        closeOverlay(s)
    }

    private fun wheelExhausted() {
        if (!day.wheelDone) {
            AppLog.i("轉盤今天的次數用完了")
            day.wheelDone = true
            store.save()
        }
    }

    /** 紅包雨還沒實際遇過，先把畫面存下來，之後再補玩法。 */
    private fun observeRain(r: Room, s: Snap) {
        for ((name, state) in r.rains) {
            val started = state.isNotEmpty() && countdown(state) == null
            val tag = "${r.key}|$name|${if (started) "start" else "seen"}"
            if (!rainSeen.add(tag)) continue
            val label = if (name == "luck_rain") "寵粉紅包雨" else "蝦皮紅包雨"
            AppLog.i("發現$label（${r.key}）：${state.ifEmpty { "沒有倒數文字" }}")
            capture("rain-$name", s)
            if (started) {  // 連拍幾張，記錄開始後的樣子
                for (i in 0 until 8) {
                    sleep(1000)
                    capture("rain-$name-$i", null)
                }
            }
        }
    }

    // ---------------- 任務獎勵面板 ----------------
    private fun panelDue(): Boolean {
        // 門檻每個週期只抽一次，避免「每跑一圈都重抽」而提早觸發
        val base = (cfg.panelEveryMinutes * 60_000).toLong()
        if (panelKey != day.lastPanel) {
            panelKey = day.lastPanel
            panelThresholdMs = Jitter.ms(base, 0.25)
        }
        if (now() - day.lastPanel > panelThresholdMs) return true
        // 觀看任務的蝦幣要當天領，23:35~23:50 之間隨機挑一個時間點再開一次
        val late = latePanelAt()
        return now() >= late && day.lastPanel < late
    }

    private var panelKey = -1L
    private var panelThresholdMs = 0L

    /** 每天固定的收尾時間（同一天內固定，換日才會變）。 */
    private fun latePanelAt(): Long {
        val minute = 35 + (day.date.hashCode().mod(16))
        return LocalDateTime.now().withHour(23).withMinute(minute).withSecond(0).withNano(0)
            .atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

    private fun pendingAmount(s: Snap): Double? {
        s.text("待提領蝦幣數")?.let { return numberNear(s, it, below = true) }
        val n = s.nodes.firstOrNull { it.text.startsWith("待提領蝦幣數:") } ?: return null  // 提領頁的寫法
        return num(n.text.substringAfter(":"))
    }

    /** 開任務獎勵面板：把觀看任務的蝦幣領進待提領，需要時提領。直播間會縮成浮窗，倒數不受影響。 */
    private fun panelTasks(final: Boolean = false) {
        val day = this.day
        day.lastPanel = now()
        store.save()
        var (scr, s) = look()
        if (scr == Screen.ROOM) {
            val banner = readRoom(s).banner
            if (banner == null) {
                AppLog.i("這間沒有任務獎勵入口，晚點再開面板")
                return
            }
            phone.tap(banner)
            val cur = waitFor(setOf(Screen.PANEL), 10_000)
            scr = cur.first
            s = cur.second
        }
        if (scr != Screen.PANEL) {
            AppLog.w("任務獎勵面板沒打開（目前在${scr.label}）")
            backToRoom(scr, s)
            return
        }
        sleep(1500)  // 等 RN 畫面載完
        s = phone.snapshot()
        val btn = s.text("領取")
        if (btn != null) {
            val amount = numberNear(s, btn, below = false) ?: 0.0
            phone.tap(btn.center)
            sleep(3000)
            s = phone.snapshot()
            day.watchTotal = round2(day.watchTotal + amount)
            AppLog.i("✔ 觀看任務 +%.2f（領進待提領）".format(amount))
        }
        // 進度條下有「N分鐘」和終點的「90分鐘」兩個標籤；只剩一個 90 的話分不出是哪個，就不更新
        val minutes = s.nodes.mapNotNull { Regex("""(\d+)分鐘""").matchEntire(it.text)?.groupValues?.get(1)?.toInt() }.sorted()
        if (minutes.size >= 2 || (minutes.isNotEmpty() && minutes[0] != 90)) day.watchMinutes = minutes[0]
        if (s.text("明天再來") != null || day.watchMinutes >= 90) day.watchDone = true
        pendingAmount(s)?.let { day.pending = it }
        store.save()
        AppLog.i("待提領 %.2f｜觀看任務 %d/90 分鐘".format(day.pending, day.watchMinutes))
        if (shouldWithdraw(day.pending, final)) withdraw(s)
        val cur = look()
        backToRoom(cur.first, cur.second)
    }

    private fun shouldWithdraw(pending: Double, final: Boolean): Boolean {
        val day = this.day
        // 提領是可選的，而且蝦皮規定每天最多 3 次
        if (!cfg.withdraw || day.withdrawals >= cfg.withdrawDailyLimit || pending < 1) return false
        val t = LocalTime.now()
        val late = t.hour == 23 && t.minute >= 40
        return pending >= cfg.withdrawMin || final || late
    }

    /** 在面板按「提領」，選不超過待提領的最大面額，送出並確認待提領有變少。 */
    private fun withdraw(panel: Snap) {
        val day = this.day
        val btn = panel.text("提領") ?: return
        phone.tap(btn.center)
        val (scr, page) = waitFor(setOf(Screen.WITHDRAW), 10_000)
        if (scr != Screen.WITHDRAW) {
            AppLog.w("提領頁沒打開（目前在${scr.label}）")
            return
        }
        sleep(1000)
        var s = phone.snapshot()
        val pending = pendingAmount(s) ?: pendingAmount(page) ?: day.pending
        val floatWin = s.id("rl_float_window")?.bounds  // 直播浮窗會蓋住部分選項
        val options = s.nodes.mapNotNull { n ->
            val amt = Regex("""(\d+)\.00""").matchEntire(n.text)?.groupValues?.get(1)?.toInt() ?: return@mapNotNull null
            if (amt > pending || (floatWin != null && floatWin.contains(n.cx, n.cy))) null else amt to n
        }
        val choice = options.maxByOrNull { it.first }
        if (choice == null) {
            AppLog.i("待提領 %.2f，沒有能選的提領金額".format(pending))
            phone.back()
            return
        }
        val (amount, node) = choice
        phone.tap(node.center)
        sleep(1000)
        val submit = phone.snapshot().text("立即提領")
        if (submit == null) {
            AppLog.w("找不到「立即提領」")
            phone.back()
            return
        }
        phone.clearToast()
        phone.tap(submit.center)
        var done = false
        for (i in 0 until 10) {
            sleep(1000)
            s = phone.snapshot()
            val confirm = s.nodes.firstOrNull { it.text in CONFIRM_TEXTS }
            if (confirm != null) {
                phone.tap(confirm.center)
                continue
            }
            // 每天只能提領 3 次；如果手動提領過，計數會少算，看到上限提示就當作今天用完
            val limit = (s.textsExcluding(emptySet()) + phone.lastToast())
                .firstOrNull { "上限" in it || ("次" in it && "已" in it && "提領" in it) }
            if (limit != null) {
                AppLog.i("提領被拒：$limit，今天不再提領")
                day.withdrawals = cfg.withdrawDailyLimit
                store.save()
                done = true
                break
            }
            val after = pendingAmount(s)
            if (after != null && after <= pending - amount + 0.01) {
                day.withdrawals++
                day.withdrawn += amount
                day.pending = after
                store.save()
                AppLog.i("✔ 提領 %d 蝦幣到「我的蝦幣」（待提領剩 %.2f，今天第 %d/3 次）".format(amount, after, day.withdrawals))
                done = true
                break
            }
        }
        if (!done) {
            AppLog.w("提領結果不明，畫面已存檔")
            capture("withdraw-unknown", s)
        }
        phone.back()
    }

    // ---------------- 主迴圈 ----------------
    fun run() {
        AppLog.i("開始：${store.summary()}")
        try {
            phone.checkActive()
            // 只有使用者按「開始」時可以從桌面或其他 App 開啟蝦皮。
            while (!phone.screenOn() || phone.locked()) {
                status("等待解鎖")
                sleep(2000)
            }
            if (phone.foreground() != SHOPEE) {
                phone.launchShopee(initial = true)
                sleep(8000)
            }
            while (true) {
                try {
                    phone.checkActive()
                    ensureApp()
                    cycle()
                } catch (e: StopBot) {
                    throw e
                } catch (e: InterruptedException) {
                    throw e
                } catch (_: AppInBackground) {
                    // 讀畫面與送出操作之間切到桌面時，回到等待流程。
                } catch (e: Exception) {
                    AppLog.e("出錯了，10 秒後繼續", e)
                    sleep(10_000)
                }
            }
        } catch (e: StopBot) {
            AppLog.e("停止：${e.message}")
        } catch (e: InterruptedException) {
            AppLog.i("已停止：${store.summary()}")
        }
    }

    private fun cycle() {
        val day = this.day
        if (day.coinLimitHit && day.wheelDone && day.watchDone) {
            restUntilTomorrow()
            return
        }
        // 剛好在一間還在倒數的直播間就先待著（離開會讓倒數重來）
        val (scr, s) = look()
        if (scr == Screen.ROOM) {
            val r = readRoom(s)
            if (r.coin.present && !day.coinLimitHit && !day.roomDone(r.key)) {
                val (reason, key) = stay("coins")
                finishRoom(setOf(key), reason, "coins")
                return
            }
        }
        val (card, purpose) = pick()
        if (card == null) {
            AppLog.i("沒有合適的直播間，2 分鐘後再找")
            status("沒有合適的直播間，稍後再找")
            sleep(120_000)
            return
        }
        val (reason, key) = stay(purpose)
        finishRoom(setOf(card.key, key), reason, purpose)
    }

    private fun finishRoom(keys0: Set<String>, reason: String, purpose: String) {
        val day = this.day
        val keys = keys0.filter { it.isNotEmpty() }
        for (k in keys) {
            val room = day.room(k)
            if (purpose == "watch") room.watched = true
            if (reason in DONE_REASONS || reason.startsWith("coin_state")) room.done = true
        }
        store.save()
        val claims = keys.maxOfOrNull { day.room(it).claims } ?: 0
        AppLog.i("離開 ${keys.sorted().joinToString("／").ifEmpty { "?" }}：${REASONS[reason] ?: reason}（這間領了 $claims 次）")
        leave()
    }

    private fun restUntilTomorrow() {
        val day = this.day
        if (!day.finalPanel) {
            goLive()
            panelTasks(final = true)
            day.finalPanel = true
            store.save()
        }
        AppLog.i("今天能領的都領完了，休息到明天：${store.summary()}")
        status("今天領完了，明天再來")
        val wake = LocalDateTime.now().plusDays(1).withHour(0).withMinute(3).withSecond(0)
            .atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
        while (now() < wake) sleep(minOf(300_000L, maxOf(1000L, wake - now())))
    }
}
