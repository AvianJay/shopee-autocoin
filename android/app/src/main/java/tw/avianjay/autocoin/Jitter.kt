package tw.avianjay.autocoin

import java.util.concurrent.ThreadLocalRandom

/**
 * 讓節奏不要那麼像機器人：所有等待與點擊都加一點隨機抖動。
 *
 * 為什麼要這樣：風控不只看你裝了什麼，也看「行為的規律性」。
 * 固定 10.5 分鐘換台、每次都點在節點正中央的同一個像素、
 * 計時器剩 2 秒準時醒來、每 30 分鐘準時開面板，
 * 這些完美週期性在伺服器端是最好抓的特徵 —— 真人不會這樣。
 */
object Jitter {
    private val rnd get() = ThreadLocalRandom.current()

    /** 基礎時間乘上 ±pct 的抖動，至少 1ms。 */
    fun ms(base: Long, pct: Double = 0.18): Long {
        if (base <= 0) return base
        return (base * (1.0 + rnd.nextDouble(-pct, pct))).toLong().coerceAtLeast(1L)
    }

    /** 同上，但回傳秒（給 Double 的設定值用）。 */
    fun secs(base: Double, pct: Double = 0.18): Double =
        if (base <= 0) base else base * (1.0 + rnd.nextDouble(-pct, pct))

    /**
     * 只往上抖動。用在「至少要等這麼久才做得完」的門檻上，
     * 例如觀看任務的 10 分鐘 —— 往下抖會讓任務做不完，往上抖只是多待一下。
     */
    fun up(base: Double, pct: Double = 0.22): Double =
        if (base <= 0) base else base * (1.0 + rnd.nextDouble(0.0, pct))

    /** [min, max] 之間的隨機整數（含頭尾）。 */
    fun int(min: Int, max: Int): Int = if (max <= min) min else rnd.nextInt(min, max + 1)

    /** 機率。 */
    fun chance(p: Double): Boolean = rnd.nextDouble() < p

    /** 把座標推到中心附近的一個隨機位置，並夾在畫面內。 */
    fun wobble(p: Pt, w: Int, h: Int, radius: Int = 9): Pt {
        val rx = Jitter.int(-radius, radius)
        val ry = Jitter.int(-radius, radius)
        return Pt(
            (p.x + rx).coerceIn(1, maxOf(1, w - 2)),
            (p.y + ry).coerceIn(1, maxOf(1, h - 2)),
        )
    }
}
