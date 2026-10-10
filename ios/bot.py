"""iOS 版主流程：往上滑換台找有「直播間蝦幣」的直播間、待著領取，定時開面板領觀看任務與提領。

跟電腦版（autocoin/bot.py）同一套流程，差別是 iOS 讀不到元件樹，畫面全靠 OCR＋顏色判斷，
而且沒有「看更多」列表，只能一間一間滑。轉盤、紅包雨在 iOS 上還沒研究，目前不處理。

手機上要先開 TouchSynthesis（Start UI Automation → Start Server），停在蝦皮的「直播」分頁，
「設定 > 螢幕顯示與亮度 > 自動鎖定」設成「永不」。

用法：
    set TS_HOST=192.168.31.196
    python ios/bot.py run [--hours 3] [--no-withdraw] [-v]
    python ios/bot.py status    今天的統計
    python ios/bot.py panel     開一次任務獎勵面板（領觀看任務、視情況提領）
    python ios/bot.py look      印出目前畫面的判讀結果（除錯用）
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autocoin.config import Config  # noqa: E402
from autocoin.state import Store  # noqa: E402
from screens import (BACK, Panel, Room, Screen, Shot, detect, money, read_panel,  # noqa: E402
                     read_room, read_withdraw)
from tsclient import TouchSynthesis  # noqa: E402

log = logging.getLogger("autocoin")

# App 內的提示文字（跟 autocoin/bot.py 同一組，只取比較短的片段，OCR 錯一兩個字也對得到）
MSG_RISK = ("不符合領取此獎勵資格", "偵測到風險", "多裝置使用上限", "裝置數限制")
MSG_DAILY_LIMIT = ("超過今天領取的次數", "達領取上限")
MSG_ROOM_DONE = ("已全部發放",)
POPUP_DISMISS = ("稍後再說",)  # 蓋住畫面的系統視窗，按這個關掉

REASONS = {
    "no_coins": "沒有直播間蝦幣",
    "room_done": "這場的蝦幣發完了",
    "daily_limit": "今天的直播間蝦幣次數用完了",
    "timeout": "待太久了",
    "watched": "觀看任務在這間已累計 10 分鐘",
    "lost": "畫面跑掉了",
}
# 因為這些原因離開的直播間，今天就不再進去
DONE_REASONS = {"no_coins", "room_done", "timeout", "watched"}

SCAN_ROOMS = 15        # 一輪最多往下滑幾間
WATCH_FROZEN = 90      # 觀看任務的倒數停住這麼久，代表這間的 10 分鐘已經算滿
SWIPE_NEXT = (187, 600, 187, 150, 0.25)


class StopBot(Exception):
    """遇到不該繼續的狀況（例如蝦皮回報帳號有風險），整個程式停下來。"""


def same(a: str, b: str) -> bool:
    """OCR 讀直播主名稱偶爾會差一兩個字（「大大的紫」讀成「大大、」），名字夠像就當同一間。"""
    a, b = re.sub(r"\W", "", a).lower(), re.sub(r"\W", "", b).lower()
    if not a or not b:
        return False
    if min(len(a), len(b)) >= 2 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.6


class Phone:
    """TouchSynthesis 連線。斷線就重連；手機上的 App 被殺掉的話要手動再按 Start Server。"""

    GIVE_UP = 30 * 60

    def __init__(self, host: str):
        self.host = host
        self.ts: TouchSynthesis | None = None

    def _call(self, fn):
        since = None
        while True:
            try:
                if self.ts is None:
                    self.ts = TouchSynthesis(self.host, timeout=40)
                    if since:
                        log.info("TouchSynthesis 連上了")
                return fn(self.ts)
            except OSError as e:  # 連線被拒、逾時、斷線（ConnectionError 也是 OSError）
                if self.ts:
                    self.ts.close()
                    self.ts = None
                if since is None:
                    since = time.time()
                    log.warning("連不到 TouchSynthesis（%s）：手機上的 App 還開著嗎？Server 有啟動嗎？", e)
                if time.time() - since > self.GIVE_UP:
                    raise StopBot("TouchSynthesis 一直連不上") from e
                time.sleep(10)

    def shot(self) -> Shot:
        return Shot(self._call(lambda ts: ts.screenshot()))

    def tap(self, x: float, y: float):
        self._call(lambda ts: ts.tap(x, y))

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.4):
        self._call(lambda ts: ts.swipe(x1, y1, x2, y2, duration))

    def close(self):
        if self.ts:
            self.ts.close()
            self.ts = None


class Bot:
    def __init__(self, cfg: Config, phone: Phone, store: Store):
        self.cfg = cfg
        self.phone = phone
        self.store = store
        self.captures = Path(cfg.log_dir) / "captures-ios"
        self.captures.mkdir(parents=True, exist_ok=True)
        self.end: float | None = None  # run(hours=…) 的結束時間
        self.first: set[str] = set()   # 還沒研究過的畫面，每次執行各存一張

    @property
    def day(self):
        return self.store.day

    # ---------------- 基本工具 ----------------
    def look(self) -> tuple[Screen, Shot]:
        s = self.phone.shot()
        # 系統的評分邀請（「喜歡「蝦皮購物」嗎？」）會蓋住整個畫面，提領完就跳出來過
        later = next((ln for ln in s.lines if ln.text in POPUP_DISMISS), None)
        if later:
            log.info("關掉彈出視窗（%s）", later.text)
            self.phone.tap(later.x, later.y)
            time.sleep(1.5)
            s = self.phone.shot()
        return detect(s), s

    def wait_for(self, wanted: set[Screen], timeout: float = 10) -> tuple[Screen, Shot]:
        deadline = time.time() + timeout
        while True:
            scr, s = self.look()
            if scr in wanted or time.time() >= deadline:
                return scr, s
            time.sleep(0.7)

    def capture(self, tag: str, s: Shot | None = None):
        """存下目前畫面（截圖＋OCR 文字），之後分析沒看過的狀態用。"""
        base = self.captures / f"{datetime.now():%m%d-%H%M%S}-{re.sub(r'[^\w.-]+', '_', tag)}"
        try:
            s = s or self.phone.shot()
            Path(f"{base}.jpg").write_bytes(s.data)
            Path(f"{base}.txt").write_text(
                "\n".join(f"({ln.x:4.0f},{ln.y:4.0f}) {ln.text}" for ln in s.lines), encoding="utf-8")
        except Exception:
            log.debug("存畫面失敗", exc_info=True)
        for old in sorted(self.captures.glob("*.jpg"))[:-300]:
            old.unlink(missing_ok=True)
            old.with_suffix(".txt").unlink(missing_ok=True)
        log.debug("已存畫面 %s", base.name)

    def capture_once(self, tag: str, s: Shot | None = None):
        if tag not in self.first:
            self.first.add(tag)
            self.capture(tag, s)

    def check_messages(self, s: Shot) -> str:
        """比對 App 的提示文字。遇到風控就停止；回傳 daily_limit / room_done / 空字串。"""
        texts = s.texts()

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

    def known(self, name: str) -> str:
        """對到今天已經記錄過的直播間就沿用原本的名字。"""
        return next((k for k in self.day.rooms if same(k, name)), name)

    def done(self, key: str) -> bool:
        return self.day.room_done(key)

    # ---------------- 導航 ----------------
    def back_to_room(self, scr: Screen, s: Shot) -> bool:
        """收掉蓋在直播間上面的頁面（面板、提領頁）。回不到直播間就回傳 False。

        認不得的畫面先多看幾眼（OCR 偶爾整頁漏讀），還是認不得才按一次返回；
        只按一次是因為不知道畫面上是什麼，可能是蝦皮被關掉、或有人在用手機。"""
        unknown = 0
        for _ in range(6):
            if scr is Screen.ROOM:
                return True
            if scr in (Screen.PANEL, Screen.WITHDRAW):
                self.phone.tap(*BACK)
            else:
                unknown += 1
                if unknown == 3:
                    self.capture("unknown", s)
                    self.phone.tap(*BACK)
                elif unknown > 3:
                    return False
            time.sleep(2)
            scr, s = self.look()
        return scr is Screen.ROOM

    def go_room(self) -> Shot:
        """回到直播間。回不去就只等、不亂點，直到有人把畫面切回蝦皮的直播間。"""
        scr, s = self.look()
        if scr is Screen.ROOM:
            return s
        if self.back_to_room(scr, s):
            return self.phone.shot()
        log.warning("回不到直播間（畫面已存檔）。不會亂點，請把手機切回蝦皮的「直播」分頁…")
        self.capture("lost", s)
        while not self.time_up():
            time.sleep(20)
            scr, s = self.look()
            if scr is Screen.ROOM:
                log.info("回到直播間，繼續")
                return s
            if scr in (Screen.PANEL, Screen.WITHDRAW):
                self.back_to_room(scr, s)
        raise StopBot("時間到了，還是回不到直播間")

    def read_current(self) -> Room:
        """讀目前這間。任務框常常晚一點才出來（或閃一下不見），沒看到直播間蝦幣就再看一次。"""
        r = read_room(self.go_room())
        if not r.coin.present or not r.key:
            time.sleep(2)
            again = read_room(self.go_room())
            if again.coin.present or not r.key:
                r = again
        return r

    def next_room(self):
        self.phone.swipe(*SWIPE_NEXT)
        time.sleep(3)

    def find_room(self) -> tuple[str, str]:
        """從目前這間開始往上滑，找今天還沒處理過、有直播間蝦幣的直播間；回傳（目的, 直播主）。
        滑了一輪都沒有的話，觀看任務還沒做完就在最後那間累積觀看時間。"""
        day = self.day
        r = Room()
        for i in range(SCAN_ROOMS):
            if i:
                self.next_room()
            r = self.read_current()
            key = self.known(r.key)
            # 沒有任務框的是短影音或沒參加活動的直播；有些直播間只有直播間蝦幣、沒有觀看任務
            if not (r.watch.present or r.coin.present) or self.done(key):
                log.debug("略過 %s（%s）", key or "?", "處理過了" if self.done(key) else "沒有任務框")
                continue
            if r.coin.present and not day.coin_limit_hit:
                log.info("→ %s 有直播間蝦幣（%s）", key or "?", r.coin.state or "?")
                return "coins", key
            if day.coin_limit_hit and not day.watch_done and not day.rooms.get(key, {}).get("watched"):
                log.info("→ %s，累積觀看任務", key or "?")
                return "watch", key
        key = self.known(r.key)
        if (not day.watch_done and r.watch.present and not self.done(key)
                and not day.rooms.get(key, {}).get("watched")):
            log.info("滑了 %d 間都沒有直播間蝦幣，在 %s 累積觀看任務", SCAN_ROOMS, key or "?")
            return "watch", key
        return "", ""

    # ---------------- 待在直播間 ----------------
    def stay(self, purpose: str, key: str) -> tuple[str, str]:
        """待在目前的直播間直到該走了。purpose：coins / watch。回傳（原因, 直播主）。"""
        cfg = self.cfg
        start = time.time()
        coin_missing = odd_state = watch_stuck = None
        watch_last, watch_since = None, time.time()
        while True:
            scr, s = self.look()
            if scr is not Screen.ROOM:
                if not self.back_to_room(scr, s):
                    return "lost", key
                continue
            r = read_room(s)
            # 不拿名字判斷有沒有被換台：iOS 只有我們自己滑才會換台，而名字的 OCR 偶爾會整串讀歪
            #（「ShhGirl女裝批發」讀成「ShhGirl*R}tt」）。直播結束的話，蝦幣不見或畫面認不得會處理
            key = key or self.known(r.key)
            day = self.day

            verdict = self.check_messages(s)
            if verdict == "room_done" or (verdict == "daily_limit" and purpose == "coins"):
                return verdict, key

            # 觀看任務：每間只算 10 分鐘，算滿之後倒數會停住不動
            secs = r.watch.seconds
            if secs != watch_last:
                watch_last, watch_since = secs, time.time()
            watch_full = secs is not None and time.time() - watch_since > WATCH_FROZEN
            # 到點會自己領，卡在「領取」太久才幫忙按
            if r.watch.claimable:
                watch_stuck = watch_stuck or time.time()
                if time.time() - watch_stuck > 15:
                    self.phone.tap(*r.watch.button)
                    watch_stuck = None
            else:
                watch_stuck = None

            if self.panel_due():
                self.panel_tasks()
                continue

            elapsed = time.time() - start
            if elapsed > cfg.max_room_minutes * 60:
                return "timeout", key
            if purpose == "watch":
                if r.coin.present and not day.coin_limit_hit:
                    log.info("這間出現直播間蝦幣，改成領蝦幣")
                    purpose = "coins"
                    continue
                if watch_full or elapsed > cfg.watch_room_minutes * 60:
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
                    self.claim(r, key)
                continue
            secs = r.coin.seconds
            if secs is None:  # 剛領完的空檔、或 OCR 沒讀到
                odd_state = odd_state or time.time()
                if time.time() - odd_state > 45:
                    self.capture(f"coin-{r.coin.state or 'blank'}", s)
                    return f"coin_state:{r.coin.state or '?'}", key
                time.sleep(2)
                continue
            odd_state = None
            time.sleep(min(max(secs - 3, 1), 20))

    def claim(self, r: Room, key: str):
        """按「直播間蝦幣」的領取，確認有成功。"""
        self.phone.tap(*r.coin.button)
        amount = None
        deadline = time.time() + 6
        s = None
        while time.time() < deadline:
            s = self.phone.shot()
            got = read_room(s).claimed
            if "成功" in got:
                amount = money(got)
                if amount is None:
                    log.warning("領取成功但金額沒讀到（%s）", got)
                    amount = 0.0
                break
            verdict = self.check_messages(s)
            if verdict:
                log.info("領取沒成功：%s", REASONS.get(verdict, verdict))
                return
        if amount is None:
            # 沒看到成功提示：倒數已經重新開始的話也算成功（金額不明）
            after = read_room(s).coin if s else None
            if after and (after.seconds or 0) > 120:
                amount = 0.0
                log.warning("沒看到成功提示，但倒數重新開始了，當作有領到")
            else:
                log.warning("直播間蝦幣按了沒反應（現在顯示：%s）",
                            (after.state if after and after.present else "不見了") or "?")
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
                 amount, key or "?", room["claims"], day.coin_claims, day.coin_total)
        # 成功提示出來後按鈕還會停在「領取」4 秒左右；等它換掉再走，免得又按一次
        # （那個位置按下去會打開任務獎勵面板）
        for _ in range(10):
            time.sleep(0.8)
            if not read_room(self.phone.shot()).coin.claimable:
                break

    # ---------------- 任務獎勵面板 ----------------
    def panel_due(self) -> bool:
        day = self.day
        if time.time() - day.last_panel > self.cfg.panel_every_minutes * 60:
            return True
        # 觀看任務的蝦幣要當天領，23:40 再開一次
        late = datetime.now().replace(hour=23, minute=40, second=0, microsecond=0)
        return datetime.now() >= late and day.last_panel < late.timestamp()

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
                log.info("這間沒有任務框，晚點再開面板")
                return
            self.phone.tap(*r.banner)
            scr, s = self.wait_for({Screen.PANEL}, 15)  # 面板是網頁，有時載得比較慢
        if scr is not Screen.PANEL:
            log.warning("任務獎勵面板沒打開（目前在%s）", scr.value)
            self.capture("panel-not-open", s)
            self.back_to_room(scr, s)
            return
        time.sleep(1.5)  # 等畫面載完
        s = self.phone.shot()
        p = read_panel(s)
        if p.claim:
            before = p.pending
            self.phone.tap(*p.claim)
            time.sleep(1.5)
            s = self.phone.shot()
            self.capture_once("panel-claimed", s)
            # 領完會跳出「0.21 蝦幣已發放至您的"待提領蝦幣數"」，提示還在的時候按別的都沒反應
            for _ in range(8):
                if not s.find("已發放至"):
                    break
                time.sleep(1)
                s = self.phone.shot()
            after = read_panel(s)
            amount = p.claim_amount
            if amount is None and before is not None and after.pending is not None:
                amount = round(after.pending - before, 2)
            day.watch_total = round(day.watch_total + (amount or 0.0), 2)
            log.info("✔ 觀看任務 +%s（領進待提領）", f"{amount:.2f}" if amount is not None else "?")
            p = after
        # 進度條下有「N分鐘」和終點的「90分鐘」兩個標籤；只剩一個 90 的話分不出是哪個，就不更新
        if len(p.minutes) >= 2 or (p.minutes and p.minutes[0] != 90):
            day.watch_minutes = p.minutes[0]
        if p.done or day.watch_minutes >= 90:
            day.watch_done = True
        if p.pending is not None:
            day.pending = p.pending
        self.store.save()
        log.info("待提領 %.2f｜觀看任務 %d/90 分鐘", day.pending, day.watch_minutes)
        if self.should_withdraw(day.pending, final):
            self.withdraw(p)
        self.back_to_room(*self.look())

    def should_withdraw(self, pending: float, final: bool) -> bool:
        cfg, day = self.cfg, self.day
        if not cfg.withdraw or day.withdrawals >= cfg.withdraw_daily_limit or pending < 1:
            return False
        late = datetime.now().hour == 23 and datetime.now().minute >= 40
        return pending >= cfg.withdraw_min or final or late

    def withdraw(self, p: Panel):
        """在面板按「提領」，選不超過待提領的最大面額，送出並確認待提領有變少。"""
        day = self.day
        if not p.withdraw:
            log.warning("面板上找不到「提領」")
            return
        for _ in range(2):  # 剛關掉的提示有時會吃掉第一下
            self.phone.tap(*p.withdraw)
            scr, s = self.wait_for({Screen.WITHDRAW}, 6)
            if scr is not Screen.PANEL:
                break
        if scr is not Screen.WITHDRAW:
            log.warning("提領頁沒打開（目前在%s）", scr.value)
            self.capture("withdraw-not-open", s)
            return
        time.sleep(1)
        w = read_withdraw(self.phone.shot())
        pending = w.pending if w.pending is not None else day.pending
        options = [(a, pos) for a, pos in w.options if a <= pending]
        if not options:
            log.info("待提領 %.2f，沒有能選的提領金額", pending)
            self.phone.tap(*BACK)
            time.sleep(2)
            return
        amount, pos = max(options)
        self.phone.tap(*pos)
        time.sleep(1)
        s = self.phone.shot()
        w = read_withdraw(s)
        if not w.submit:
            log.warning("找不到「立即提領」")
            self.phone.tap(*BACK)
            time.sleep(2)
            return
        self.capture_once("withdraw-selected", s)
        self.phone.tap(*w.submit)
        for i in range(10):
            time.sleep(1)
            scr, s = self.look()
            if i == 1:
                self.capture_once("withdraw-submitted", s)
            confirm = next((ln for ln in s.lines if ln.text in ("確認", "確定", "確認提領")), None)
            if confirm:
                self.phone.tap(confirm.x, confirm.y)
                continue
            # 每天只能提領 3 次；手動提領過的話程式的計數會少算，看到上限提示就當作今天用完
            # （只看提領頁和面板，直播間的宣傳圖上常有「上限」之類的字）
            limit = next((ln.text for ln in s.lines if scr is not Screen.ROOM and (
                "上限" in ln.text or ("次" in ln.text and "已" in ln.text and "提領" in ln.text))),
                None)
            if limit:
                log.info("提領被拒：%s，今天不再提領", limit)
                day.withdrawals = self.cfg.withdraw_daily_limit
                self.store.save()
                break
            if scr is Screen.WITHDRAW:
                after = read_withdraw(s).pending
            elif scr is Screen.PANEL:
                after = read_panel(s).pending
            else:
                after = None
            if s.find("提領成功") and after is None:
                after = round(pending - amount, 2)
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
        if scr is Screen.WITHDRAW:
            self.phone.tap(*BACK)
            time.sleep(2)

    # ---------------- 主迴圈 ----------------
    def run(self, hours: float | None = None):
        self.end = time.time() + hours * 3600 if hours else None
        log.info("開始：%s", self.store.summary())
        try:
            while not self.time_up():
                try:
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
            self.go_room()
            self.panel_tasks(final=True)
        except Exception:
            log.exception("收尾開面板失敗")
        log.info("結束：%s", self.store.summary())

    def cycle(self):
        day = self.day
        if day.coin_limit_hit and day.watch_done:
            self.rest_until_tomorrow()
            return
        purpose, key = self.find_room()
        if not purpose:
            log.info("滑了 %d 間都沒有合適的直播間，1 分鐘後繼續往下找", SCAN_ROOMS)
            time.sleep(60)
            self.next_room()
            return
        reason, key = self.stay(purpose, key)
        self.finish_room(key, reason, purpose)

    def finish_room(self, key: str, reason: str, purpose: str):
        day = self.day
        if key:
            room = day.room(key)
            if purpose == "watch" or reason == "watched":
                room["watched"] = True
            if reason in DONE_REASONS or reason.startswith("coin_state"):
                room["done"] = True
            self.store.save()
        claims = day.rooms.get(key, {}).get("claims", 0)
        log.info("離開 %s：%s（這間領了 %d 次）｜%s", key or "?", REASONS.get(reason, reason),
                 claims, self.store.summary())

    def rest_until_tomorrow(self):
        day = self.day
        if not day.final_panel:
            self.go_room()
            self.panel_tasks(final=True)
            day.final_panel = True
            self.store.save()
        log.info("今天能領的都領完了，休息到明天：%s", self.store.summary())
        wake = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=3, second=0, microsecond=0)
        while datetime.now() < wake and not self.time_up():
            time.sleep(min(300, max(1, (wake - datetime.now()).total_seconds())))

    def time_up(self) -> bool:
        return self.end is not None and time.time() >= self.end


def setup_logging(log_dir: Path, verbose: bool):
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname).1s %(message)s", "%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    file = RotatingFileHandler(log_dir / "ios.log", maxBytes=5_000_000, backupCount=3,
                               encoding="utf-8")
    file.setFormatter(fmt)
    file.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file)
    for noisy in ("PIL", "asyncio"):  # OCR 每次都會用 asyncio.run，會一直印 Using proactor
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    p = argparse.ArgumentParser(prog="ios/bot.py", description="蝦皮直播自動領蝦幣（iOS，TouchSynthesis）")
    p.add_argument("command", nargs="?", default="run", choices=["run", "status", "panel", "look"],
                   help="run：開始自動領（預設）；status：今天的統計；"
                        "panel：開一次任務獎勵面板；look：印出目前畫面的判讀結果")
    p.add_argument("--host", default=os.environ.get("TS_HOST", "127.0.0.1"),
                   help="手機的 IP（預設讀環境變數 TS_HOST）")
    p.add_argument("--hours", type=float, help="跑幾個小時後停止（預設一直跑）")
    p.add_argument("--no-withdraw", action="store_true", help="不要自動提領")
    p.add_argument("-v", "--verbose", action="store_true", help="畫面上也顯示除錯訊息")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    cfg = Config()
    if args.no_withdraw:
        cfg.withdraw = False
    cfg.state_path = str(ROOT / "state-ios.json")  # iOS 跟 Android 是不同的蝦皮帳號，分開記
    cfg.log_dir = str(ROOT / cfg.log_dir)
    setup_logging(Path(cfg.log_dir), args.verbose)

    store = Store(cfg.state_path)
    if args.command == "status":
        print(store.summary())
        return

    phone = Phone(args.host)
    bot = Bot(cfg, phone, store)
    try:
        if args.command == "look":
            scr, s = bot.look()
            print(scr.value)
            if scr is Screen.ROOM:
                print(read_room(s))
            elif scr is Screen.PANEL:
                print(read_panel(s))
            elif scr is Screen.WITHDRAW:
                print(read_withdraw(s))
            for ln in s.lines:
                print(f"  ({ln.x:4.0f},{ln.y:4.0f}) {ln.text}")
        elif args.command == "panel":
            bot.go_room()
            bot.panel_tasks()
            print(store.summary())
        else:
            bot.run(args.hours)
    except KeyboardInterrupt:
        logging.getLogger("autocoin").info("手動停止：%s", store.summary())
    except StopBot as e:
        logging.getLogger("autocoin").error("停止：%s", e)
    finally:
        phone.close()


if __name__ == "__main__":
    main()
