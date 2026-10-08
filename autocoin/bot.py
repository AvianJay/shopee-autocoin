"""主流程：挑有蝦幣的直播間、待著領取、轉盤、定時開面板領觀看任務與提領。"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .device import Device
from .screens import Card, Room, Screen, detect, inside, num, number_near, read_cards, read_room
from .state import Store
from .ui import PKG, Snapshot

log = logging.getLogger("autocoin")

# App 內的提示文字（取自 APK 的 zh-rTW 字串）
MSG_RISK = ("不符合領取此獎勵資格", "偵測到風險活動", "您已達多裝置使用上限", "已達裝置數限制")
MSG_DAILY_LIMIT = ("已超過今天領取的次數限制", "您已達領取上限")
MSG_ROOM_DONE = ("直播蝦幣已全部發放",)
MSG_WHEEL_LIMIT = ("達到每日參與上限", "達到每日上限")
# 觀眾、直播主可以自由打字的地方，比對提示文字時要略過
FREE_TEXT_IDS = ("public_screen_view", "danmaku_view", "scr_pin", "tv_live_title", "tv_anchor_name")

REASONS = {
    "no_coins": "沒有直播間蝦幣",
    "room_done": "這場的蝦幣發完了",
    "daily_limit": "今天的直播間蝦幣次數用完了",
    "timeout": "待太久了",
    "watched": "觀看任務在這間已累計 10 分鐘",
    "wheel_checked": "轉盤處理完了",
    "lost": "畫面跑掉了",
    "switched": "直播間被換掉了",
}
# 因為這些原因離開的直播間，今天就不再進去
DONE_REASONS = {"no_coins", "room_done", "timeout", "watched", "wheel_checked"}


class StopBot(Exception):
    """遇到不該繼續的狀況（例如蝦皮回報帳號有風險），整個程式停下來。"""


class Bot:
    def __init__(self, cfg: Config, dev: Device, store: Store):
        self.cfg = cfg
        self.dev = dev
        self.store = store
        self.captures = Path(cfg.log_dir) / "captures"
        self.captures.mkdir(parents=True, exist_ok=True)
        self.home_pkg = dev.home_package()
        self.rain_seen: set[tuple[str, str, str]] = set()
        self.end: float | None = None  # run(hours=…) 的結束時間

    @property
    def day(self):
        return self.store.day

    # ---------------- 基本工具 ----------------
    def look(self) -> tuple[Screen, Snapshot]:
        s = self.dev.snapshot()
        return detect(s), s

    def wait_for(self, wanted: set[Screen], timeout: float = 10) -> tuple[Screen, Snapshot]:
        deadline = time.time() + timeout
        while True:
            scr, s = self.look()
            if scr in wanted or time.time() >= deadline:
                return scr, s
            time.sleep(0.7)

    def capture(self, tag: str, s: Snapshot | None = None):
        """存下目前畫面（截圖＋UI 樹），之後分析沒看過的狀態用。"""
        base = self.captures / f"{datetime.now():%m%d-%H%M%S}-{re.sub(r'[^\w.-]+', '_', tag)}"
        try:
            self.dev.screenshot(f"{base}.png")
            s = s or self.dev.snapshot()
            Path(f"{base}.xml").write_text(s.xml, encoding="utf-8")
        except Exception:
            log.debug("存畫面失敗", exc_info=True)
        for old in sorted(self.captures.glob("*.png"))[:-300]:
            old.unlink(missing_ok=True)
            old.with_suffix(".xml").unlink(missing_ok=True)
        log.debug("已存畫面 %s", base.name)

    def check_messages(self, s: Snapshot, toast: str = "") -> str:
        """比對 App 的提示文字。遇到風控就停止；回傳 daily_limit / room_done / 空字串。"""
        texts = s.texts_excluding(FREE_TEXT_IDS) + ([toast] if toast else [])

        def hit(keys):
            return next((t for t in texts if any(k in t for k in keys)), None)

        if t := hit(MSG_RISK):
            self.capture("risk", s)
            raise StopBot(f"蝦皮回報「{t}」，為了帳號安全先停止")
        if t := hit(MSG_DAILY_LIMIT):
            if not self.day.coin_limit_hit:
                log.info("直播間蝦幣今天的次數用完了（%s）", t)
                self.day.coin_limit_hit = True
                self.store.save()
            return "daily_limit"
        if hit(MSG_ROOM_DONE):
            return "room_done"
        return ""

    # ---------------- 讓蝦皮待在前景 ----------------
    def ensure_app(self):
        waiting = ""
        while True:
            if not self.dev.screen_on():
                log.info("螢幕關著，喚醒")
                self.dev.wake()
                time.sleep(2)
            if self.dev.locked():
                if waiting != "locked":
                    log.warning("手機鎖定中，等解鎖…（建議開「充電時保持螢幕常亮」）")
                    waiting = "locked"
                time.sleep(30)
                continue
            pkg = self.dev.current().get("package", "")
            if pkg == PKG:
                if waiting:
                    log.info("蝦皮回到前景，繼續")
                return
            if not pkg or pkg == self.home_pkg:
                log.info("蝦皮不在前景，重新開啟")
                self.dev.launch()
                time.sleep(8)
                continue
            if pkg in ("android", "com.android.intentresolver"):  # 分享選單之類的系統視窗
                self.dev.back()
                time.sleep(1)
                continue
            if waiting != pkg:
                log.info("前景是 %s，可能有人在用手機，等它結束…", pkg)
                waiting = pkg
            time.sleep(30)

    # ---------------- 導航 ----------------
    def close_overlay(self, s: Snapshot):
        btn = s.id("ic_close")
        if btn:
            self.dev.tap_node(btn)
        else:
            self.dev.back()

    def close_explore(self, s: Snapshot):
        btn = s.id("collapse_icon")
        if btn:
            self.dev.tap_node(btn)
        else:
            self.dev.back()

    def go_live(self) -> Snapshot:
        """不管現在在哪，回到某個直播間。"""
        for _ in range(2):
            for _ in range(10):
                scr, s = self.look()
                if scr is Screen.ROOM:
                    return s
                log.debug("go_live：目前在%s", scr.value)
                if scr is Screen.LIVE_PREVIEW:
                    self.dev.swipe(540, 1800, 540, 600, 0.3)
                elif scr is Screen.VIDEO_TAB:
                    self.dev.tap_node(s.desc("click 1 tab"))
                elif scr is Screen.HOME:
                    self.dev.tap_node(s.desc("tab_bar_button_video_and_live"))
                elif scr is Screen.EXPLORE:
                    self.close_explore(s)
                elif scr in (Screen.WHEEL, Screen.WHEEL_RESULT):
                    self.close_overlay(s)
                elif scr is Screen.FOLLOW_DIALOG:
                    self.dev.tap_node(s.id("tv_cancel"))
                elif scr is Screen.OTHER_APP:
                    self.ensure_app()
                else:
                    self.dev.back()
                time.sleep(2.5)
            log.warning("一直回不到直播間，重新啟動蝦皮")
            self.capture("lost", s)
            self.dev.restart_app()
            time.sleep(10)
            self.ensure_app()
        raise RuntimeError("回不到直播間")

    def back_to_room(self, scr: Screen, s: Snapshot) -> bool:
        """收掉蓋在直播間上面的東西（轉盤、面板、提領頁…）。回不到直播間就回傳 False。"""
        for _ in range(4):
            if scr is Screen.ROOM:
                return True
            if scr in (Screen.WHEEL, Screen.WHEEL_RESULT):
                self.close_overlay(s)
            elif scr is Screen.FOLLOW_DIALOG:
                self.dev.tap_node(s.id("tv_cancel"))
            elif scr in (Screen.PANEL, Screen.WITHDRAW, Screen.WEB, Screen.UNKNOWN):
                if scr is Screen.UNKNOWN:
                    self.capture("unknown", s)
                self.dev.back()
            elif scr is Screen.OTHER_APP:
                self.ensure_app()
            else:
                return False
            time.sleep(2)
            scr, s = self.look()
        return scr is Screen.ROOM

    def open_explore(self) -> Snapshot | None:
        """打開直播間右上角的「看更多」列表。"""
        for _ in range(3):
            scr, s = self.look()
            if scr is not Screen.EXPLORE:
                if scr is not Screen.ROOM:
                    s = self.go_live()
                r = read_room(s)
                if r.explore:
                    self.dev.tap(*r.explore)
                elif r.close:  # 沒有「看更多」的獨立直播間，先關掉
                    self.dev.tap(*r.close)
                    time.sleep(2.5)
                    continue
                else:
                    log.warning("直播間沒有「看更多」")
                    self.capture("no-explore", s)
                    return None
                scr, s = self.wait_for({Screen.EXPLORE}, 8)
                if scr is not Screen.EXPLORE:
                    continue
                time.sleep(2)  # 廣告彈窗通常晚一點才出來
                s = self.dev.snapshot()
            if self.dismiss_popup(s):
                time.sleep(1)
                s = self.dev.snapshot()
            return s
        return None

    def dismiss_popup(self, s: Snapshot) -> bool:
        """「看更多」列表上偶爾蓋著廣告：一張大圖＋下面一個小關閉鈕，兩者都沒有文字或 ID。"""
        bare = [n for n in s.nodes if n.clickable and not (n.rid or n.text or n.desc)]
        big = next((n for n in bare if n.width > 600 and n.height > 700), None)
        if not big:
            return False
        close = next((n for n in bare if 60 <= n.width <= 150 and 60 <= n.height <= 150
                      and n.bounds[1] >= big.bounds[3] - 30), None)
        if not close:
            return False
        log.info("關掉廣告彈窗")
        self.dev.tap_node(close)
        return True

    def scroll_top(self, s: Snapshot) -> Snapshot:
        for _ in range(self.cfg.explore_pages + 3):
            if any(c.rid == "live_card_cover_0" for c in read_cards(s)):
                break
            self.dev.swipe(590, 700, 590, 2000, 0.3)
            time.sleep(0.8)
            s = self.dev.snapshot()
        return s

    def scan(self, s: Snapshot) -> dict[str, Card]:
        """往下捲「看更多」列表，收集卡片（rid → Card）。"""
        cards: dict[str, Card] = {}
        for page in range(self.cfg.explore_pages + 1):
            new = 0
            for c in read_cards(s):
                if not c.key:
                    continue
                if c.rid not in cards:
                    new += 1
                # 卡片露出越多資訊越完整（標題、直播主）
                if c.rid not in cards or c.height > cards[c.rid].height:
                    cards[c.rid] = c
            if page == self.cfg.explore_pages or (page and not new):
                break
            self.dev.swipe(590, 1900, 590, 700, 0.5)
            time.sleep(1.2)
            s = self.dev.snapshot()
        return cards

    def choose(self, cards: dict[str, Card]) -> tuple[Card | None, str]:
        """挑下一間：還能轉盤就先去「抽獎」的；再來挑「最多X」最高的；都沒有就去累積觀看任務。"""
        day = self.day

        def flag(c: Card, name: str) -> bool:
            return day.rooms.get(c.key, {}).get(name, False)

        fresh = [c for c in cards.values() if not day.room_done(c.key)]
        if not day.wheel_done:
            lottery = [c for c in fresh if c.lottery and not flag(c, "wheel")]
            if lottery:
                return lottery[0], ("wheel" if day.coin_limit_hit else "coins")
        if not day.coin_limit_hit:
            rich = [c for c in fresh if (c.max_coins or 0) >= self.cfg.min_room_coins]
            if rich:
                return max(rich, key=lambda c: c.max_coins), "coins"
        if not day.watch_done:
            unwatched = [c for c in fresh if not flag(c, "watched")]
            if unwatched:
                return unwatched[0], "watch"
        return None, ""

    def enter(self, card: Card) -> Snapshot | None:
        """捲回那張卡片並點進去。"""
        for _ in range(self.cfg.explore_pages * 2 + 2):
            s = self.dev.snapshot()
            hit = next((c for c in read_cards(s) if c.rid == card.rid and c.height >= 300), None)
            if hit:
                self.dev.tap(*hit.center)
                scr, s = self.wait_for({Screen.ROOM}, 15)
                return s if scr is Screen.ROOM else None
            self.dev.swipe(590, 800, 590, 1700, 0.5)  # 往回捲
            time.sleep(1)
        log.warning("列表裡找不到 %s", card.key)
        return None

    def pick(self) -> tuple[Card | None, str]:
        s = self.open_explore()
        if s is None:
            return None, ""
        cards = self.scan(self.scroll_top(s))
        card, purpose = self.choose(cards)
        if not card:
            self.close_explore(self.dev.snapshot())
            return None, ""
        badge = f"最多 {card.max_coins:g}" if card.max_coins else ("抽獎" if card.lottery else "-")
        log.info("→ 進入 %s（%s，%s 人在看）目的：%s", card.key, badge, card.viewers, purpose)
        if self.enter(card) is None:
            return None, ""
        return card, purpose

    def leave(self):
        """離開目前的直播間。獨立直播間按 X；首頁直播分頁裡那間不用關，下次直接開「看更多」。"""
        scr, s = self.look()
        if scr is Screen.ROOM:
            r = read_room(s)
            if r.close:
                self.dev.tap(*r.close)
                time.sleep(2.5)

    # ---------------- 待在直播間 ----------------
    def stay(self, purpose: str) -> tuple[str, str]:
        """待在目前的直播間直到該走了。purpose：coins / wheel / watch。回傳（原因, 直播主）。"""
        cfg = self.cfg
        start = time.time()
        coin_missing = odd_state = watch_stuck = None
        wheel_tries = 0
        wheel_settled = False  # 這間的轉盤已經看到結果了
        last_wheel = ""
        key = ""
        while True:
            scr, s = self.look()
            if scr is not Screen.ROOM:
                if not self.back_to_room(scr, s):
                    return "lost", key
                continue
            r = read_room(s)
            if key and r.key and r.key != key:
                return "switched", key
            if not key:
                log.debug("進房：%s｜直播間蝦幣 %s %s｜轉盤 %r｜紅包雨 %s",
                          r.key, r.coin.coin, r.coin.state or "-", r.wheel, r.rains or "-")
            elif r.wheel != last_wheel:
                log.debug("轉盤狀態：%r → %r", last_wheel, r.wheel)
            last_wheel = r.wheel
            key = key or r.key
            day = self.day

            verdict = self.check_messages(s)
            if verdict == "room_done" or (verdict == "daily_limit" and purpose == "coins"):
                return verdict, key

            # 幸運轉盤：每間一次，每天 10 次。「已參加」只代表關注過，不代表轉過
            if (r.wheel in ("參加", "已參加") and not wheel_settled and not day.wheel_done
                    and wheel_tries < 2):
                wheel_tries += 1
                wheel_settled = self.play_wheel(r)
                continue
            if purpose == "wheel":
                return "wheel_checked", key

            if r.rains:
                self.observe_rain(r, s)

            # 觀看任務到點會自己領，卡在「領取」太久才幫忙按
            if r.watch.claimable:
                watch_stuck = watch_stuck or time.time()
                if time.time() - watch_stuck > 15:
                    self.dev.tap(*r.watch.button)
                    watch_stuck = None
            else:
                watch_stuck = None
            if r.watch.state == "明天再來" and not day.watch_done:
                log.info("觀看任務今天做完了")
                day.watch_done = True
                self.store.save()

            if self.panel_due():
                self.panel_tasks()
                continue

            elapsed = time.time() - start
            if elapsed > cfg.max_room_minutes * 60:
                return "timeout", key
            if purpose == "watch":
                if elapsed > cfg.watch_room_minutes * 60:
                    return "watched", key
                time.sleep(20)
                continue

            # 直播間蝦幣
            if not r.coin.present:
                coin_missing = coin_missing or time.time()
                if time.time() - coin_missing > cfg.no_coin_grace:
                    return "no_coins", key
                time.sleep(3)
                continue
            coin_missing = None
            if r.coin.claimable:
                if "成功" in r.claimed:  # 上一次的成功提示還在，按鈕還沒換掉
                    time.sleep(1)
                else:
                    self.claim(r)
                continue
            secs = r.coin.seconds
            if secs is None:
                odd_state = odd_state or time.time()
                if time.time() - odd_state > 30:
                    self.capture(f"coin-{r.coin.state}", s)
                    return f"coin_state:{r.coin.state}", key
                time.sleep(3)
                continue
            odd_state = None
            nap = secs - 2
            for state in r.rains.values():  # 紅包雨快開始時要醒著
                m = re.fullmatch(r"(\d+):(\d\d)", state)
                if m:
                    nap = min(nap, int(m[1]) * 60 + int(m[2]) - 2)
            time.sleep(min(max(nap, 1), 20))

    def claim(self, r: Room):
        """按「直播間蝦幣」的領取，確認有成功。"""
        key, before = r.key, r.coin.coin or 0.0
        self.dev.clear_toast()
        self.dev.tap(*r.coin.button)
        amount = None
        deadline = time.time() + 8
        while time.time() < deadline:
            s = self.dev.snapshot()
            got = read_room(s).claimed
            if "成功" in got:
                amount = num(got) or before
                break
            verdict = self.check_messages(s, self.dev.last_toast())
            if verdict:
                log.info("領取沒成功：%s", REASONS.get(verdict, verdict))
                return
            time.sleep(0.3)
        if amount is None:
            # 沒看到成功提示：倒數已經重新開始的話也算成功
            s = self.dev.snapshot()
            after = read_room(s).coin
            if (after.seconds or 0) > 120:
                amount = before
            else:
                log.warning("直播間蝦幣按了沒反應（現在顯示：%s）", after.state or "不見了")
                self.capture("claim-unknown", s)
                return
        day = self.day
        room = day.room(key)
        room["claims"] += 1
        room["coins"] = round(room["coins"] + amount, 2)
        day.coin_claims += 1
        day.coin_total = round(day.coin_total + amount, 2)
        self.store.save()
        log.info("✔ 直播間蝦幣 +%.2f｜%s 第 %d 次｜今天 %d 次，共 %.2f",
                 amount, key, room["claims"], day.coin_claims, day.coin_total)
        # 成功提示出來時按鈕常常還停在「領取」；等它換掉再走，免得又按一次
        # （那個位置按下去會打開任務獎勵面板）
        for _ in range(10):
            time.sleep(0.8)
            if not read_room(self.dev.snapshot()).coin.claimable:
                break

    def play_wheel(self, r: Room) -> bool:
        """轉這間的幸運轉盤。看到結果（這次轉的或之前轉過的）就回傳 True。"""
        key = r.key
        self.day.room(key)["wheel"] = True
        self.store.save()
        self.dev.tap(*r.wheel_button)
        scr, s = self.wait_for({Screen.WHEEL, Screen.WHEEL_RESULT}, 6)
        spins = 0
        for _ in range(6):
            if scr is Screen.WHEEL_RESULT:
                self.wheel_result(s, key)
                return True
            if scr is Screen.FOLLOW_DIALOG:
                log.info("轉盤限粉絲，關注 %s", key)
                self.dev.tap_node(s.id("tv_confirm"))
                # 對話框關掉後它的空殼視窗會擋一陣子，期間讀不到底下的轉盤（轉盤其實還開著）
                scr, s = self.wait_for({Screen.WHEEL, Screen.WHEEL_RESULT}, 8)
                if scr is Screen.UNKNOWN:
                    scr = Screen.WHEEL
                continue
            if scr is not Screen.WHEEL or spins >= 3:
                break
            if any(k in t for t in s.texts_excluding(()) for k in MSG_WHEEL_LIMIT):
                self.wheel_exhausted()
                break
            # 「開始」沒有獨立節點，按轉盤正中央
            light = s.id("iv_draw_light")
            self.dev.tap(*(light.center if light else (540, 1100)))
            spins += 1
            scr, s = self.wait_for({Screen.WHEEL_RESULT, Screen.FOLLOW_DIALOG}, 12)
        log.info("轉盤沒有結果（可能還沒開始或已結束）")
        self.capture("wheel-unknown", s)
        if scr is not Screen.ROOM:
            self.close_overlay(s)
        return False

    def wheel_result(self, s: Snapshot, key: str):
        result, more = s.id("tv_result"), s.id("tv_more")
        text = result.text if result else ""
        amount = num(text) if "獲得" in text else None
        left = num(more.text) if more else None
        day = self.day
        room = day.room(key)
        # 每間只能轉一次，第一次看到結果就記帳（按「關注」時可能就直接轉了，當下讀不到結果）
        if amount is not None and not room.get("wheel_got"):
            room["wheel_got"] = True
            day.wheel_spins += 1
            day.wheel_total = round(day.wheel_total + amount, 2)
            log.info("✔ 轉盤 +%.2f｜%s｜今天還能轉 %s 次", amount, key,
                     int(left) if left is not None else "?")
        elif text:
            log.info("轉盤：%s", text)
        if left == 0 or any(k in text for k in MSG_WHEEL_LIMIT):
            self.wheel_exhausted()
        self.store.save()
        self.close_overlay(s)

    def wheel_exhausted(self):
        if not self.day.wheel_done:
            log.info("轉盤今天的次數用完了")
            self.day.wheel_done = True
            self.store.save()

    def observe_rain(self, r: Room, s: Snapshot):
        """紅包雨還沒實際遇過，先把畫面存下來，之後再補玩法。"""
        for name, state in r.rains.items():
            started = bool(state) and not re.fullmatch(r"\d+:\d\d", state)
            tag = (r.key, name, "start" if started else "seen")
            if tag in self.rain_seen:
                continue
            self.rain_seen.add(tag)
            label = "寵粉紅包雨" if name == "luck_rain" else "蝦皮紅包雨"
            log.info("發現%s（%s）：%s", label, r.key, state or "沒有倒數文字")
            self.capture(f"rain-{name}", s)
            if started:  # 連拍幾張，記錄開始後的樣子
                for i in range(8):
                    time.sleep(1)
                    self.capture(f"rain-{name}-{i}")

    # ---------------- 任務獎勵面板 ----------------
    def panel_due(self) -> bool:
        day = self.day
        if time.time() - day.last_panel > self.cfg.panel_every_minutes * 60:
            return True
        # 觀看任務的蝦幣要當天領，23:40 之後一定再開一次
        late = datetime.now().replace(hour=23, minute=40, second=0, microsecond=0)
        return datetime.now() >= late and day.last_panel < late.timestamp()

    def pending_amount(self, s: Snapshot) -> float | None:
        label = s.text("待提領蝦幣數")
        if label:
            return number_near(s, label, below=True)
        n = next((n for n in s.nodes if n.text.startswith("待提領蝦幣數:")), None)  # 提領頁的寫法
        return num(n.text.split(":", 1)[1]) if n else None

    def panel_tasks(self, final: bool = False):
        """開任務獎勵面板：把觀看任務的蝦幣領進待提領，需要時提領。
        直播間會縮成右下角浮窗，倒數不受影響。"""
        day = self.day
        day.last_panel = time.time()
        self.store.save()
        scr, s = self.look()
        if scr is Screen.ROOM:
            r = read_room(s)
            if not r.banner:
                log.info("這間沒有任務獎勵入口，晚點再開面板")
                return
            self.dev.tap(*r.banner)
            scr, s = self.wait_for({Screen.PANEL}, 10)
        if scr is not Screen.PANEL:
            log.warning("任務獎勵面板沒打開（目前在%s）", scr.value)
            self.back_to_room(scr, s)
            return
        time.sleep(1.5)  # 等 RN 畫面載完
        s = self.dev.snapshot()
        btn = s.text("領取")
        if btn:
            amount = number_near(s, btn, below=False) or 0.0
            self.dev.tap_node(btn)
            time.sleep(3)
            s = self.dev.snapshot()
            day.watch_total = round(day.watch_total + amount, 2)
            log.info("✔ 觀看任務 +%.2f（領進待提領）", amount)
        # 進度條下有「N分鐘」和終點的「90分鐘」兩個標籤；只剩一個 90 的話分不出是哪個，就不更新
        minutes = sorted(int(n.text[:-2]) for n in s.nodes if re.fullmatch(r"\d+分鐘", n.text))
        if len(minutes) >= 2 or (minutes and minutes[0] != 90):
            day.watch_minutes = minutes[0]
        if s.text("明天再來") or day.watch_minutes >= 90:
            day.watch_done = True
        pending = self.pending_amount(s)
        if pending is not None:
            day.pending = pending
        self.store.save()
        log.info("待提領 %.2f｜觀看任務 %d/90 分鐘", day.pending, day.watch_minutes)
        if self.should_withdraw(day.pending, final):
            self.withdraw(s)
        self.back_to_room(*self.look())

    def should_withdraw(self, pending: float, final: bool) -> bool:
        cfg, day = self.cfg, self.day
        if not cfg.withdraw or day.withdrawals >= cfg.withdraw_daily_limit or pending < 1:
            return False
        late = datetime.now().hour == 23 and datetime.now().minute >= 40
        return pending >= cfg.withdraw_min or final or late

    def withdraw(self, s: Snapshot):
        """在面板按「提領」，選不超過待提領的最大面額，送出並確認待提領有變少。"""
        day = self.day
        btn = s.text("提領")
        if not btn:
            return
        self.dev.tap_node(btn)
        scr, s = self.wait_for({Screen.WITHDRAW}, 10)
        if scr is not Screen.WITHDRAW:
            log.warning("提領頁沒打開（目前在%s）", scr.value)
            return
        time.sleep(1)
        s = self.dev.snapshot()
        pending = self.pending_amount(s) or day.pending
        float_win = s.id("rl_float_window")  # 直播浮窗會蓋住部分選項
        options = []
        for n in s.nodes:
            m = re.fullmatch(r"(\d+)\.00", n.text)
            if m and int(m[1]) <= pending and not (float_win and inside(n.center, float_win.bounds)):
                options.append((int(m[1]), n))
        if not options:
            log.info("待提領 %.2f，沒有能選的提領金額", pending)
            self.dev.back()
            return
        amount, node = max(options, key=lambda t: t[0])
        self.dev.tap_node(node)
        time.sleep(1)
        submit = self.dev.snapshot().text("立即提領")
        if not submit:
            log.warning("找不到「立即提領」")
            self.dev.back()
            return
        self.dev.clear_toast()
        self.dev.tap_node(submit)
        for _ in range(10):
            time.sleep(1)
            scr, s = self.look()
            confirm = next((n for n in s.nodes if n.text in ("確認", "確定", "確認提領")), None)
            if confirm:
                self.dev.tap_node(confirm)
                continue
            # 每天只能提領 3 次；如果手動提領過，程式的計數會少算，看到上限提示就當作今天用完
            limit = next((t for t in s.texts_excluding(()) + [self.dev.last_toast()]
                          if "上限" in t or ("次" in t and "已" in t and "提領" in t)), None)
            if limit:
                log.info("提領被拒：%s，今天不再提領", limit)
                day.withdrawals = self.cfg.withdraw_daily_limit
                self.store.save()
                break
            after = self.pending_amount(s)
            if after is not None and after <= pending - amount + 0.01:
                day.withdrawals += 1
                day.withdrawn += amount
                day.pending = after
                self.store.save()
                log.info("✔ 提領 %d 蝦幣到「我的蝦幣」（待提領剩 %.2f，今天第 %d 次）",
                         amount, after, day.withdrawals)
                break
        else:
            log.warning("提領結果不明，畫面已存檔")
            self.capture("withdraw-unknown", s)
        self.dev.back()

    # ---------------- 主迴圈 ----------------
    def run(self, hours: float | None = None):
        self.end = time.time() + hours * 3600 if hours else None
        log.info("開始：%s", self.store.summary())
        try:
            while not self.time_up():
                try:
                    self.ensure_app()
                    self.cycle()
                except StopBot:
                    raise
                except Exception:
                    log.exception("出錯了，10 秒後繼續")
                    time.sleep(10)
        except StopBot as e:
            log.error("停止：%s", e)
            return
        # 正常結束前把觀看任務的蝦幣領掉（隔天就作廢了）
        try:
            self.go_live()
            self.panel_tasks(final=True)
        except Exception:
            log.exception("收尾開面板失敗")
        log.info("結束：%s", self.store.summary())

    def cycle(self):
        day = self.day
        if day.coin_limit_hit and day.wheel_done and day.watch_done:
            self.rest_until_tomorrow()
            return
        # 剛好在一間還在倒數的直播間就先待著（離開會讓倒數重來）
        scr, s = self.look()
        if scr is Screen.ROOM:
            r = read_room(s)
            if r.coin.present and not day.coin_limit_hit and not day.room_done(r.key):
                reason, key = self.stay("coins")
                self.finish_room({key}, reason, "coins")
                return
        card, purpose = self.pick()
        if not card:
            log.info("沒有合適的直播間，2 分鐘後再找")
            time.sleep(120)
            return
        reason, key = self.stay(purpose)
        self.finish_room({card.key, key}, reason, purpose)

    def finish_room(self, keys: set[str], reason: str, purpose: str):
        day = self.day
        keys = {k for k in keys if k}
        for k in keys:
            room = day.room(k)
            if purpose == "watch":
                room["watched"] = True
            if reason in DONE_REASONS or reason.startswith("coin_state"):
                room["done"] = True
        self.store.save()
        claims = max((day.rooms[k]["claims"] for k in keys), default=0)
        log.info("離開 %s：%s（這間領了 %d 次）｜%s", "／".join(sorted(keys)) or "?",
                 REASONS.get(reason, reason), claims, self.store.summary())
        self.leave()

    def rest_until_tomorrow(self):
        day = self.day
        if not day.final_panel:
            self.go_live()
            self.panel_tasks(final=True)
            day.final_panel = True
            self.store.save()
        log.info("今天能領的都領完了，休息到明天：%s", self.store.summary())
        wake = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=3, second=0, microsecond=0)
        while datetime.now() < wake and not self.time_up():
            time.sleep(min(300, max(1, (wake - datetime.now()).total_seconds())))

    def time_up(self) -> bool:
        return self.end is not None and time.time() >= self.end
