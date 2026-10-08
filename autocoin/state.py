"""每日統計與處理過的直播間，存成 JSON，重開程式也記得。"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Day:
    date: str
    coin_claims: int = 0            # 直播間蝦幣領取次數（官方上限每天 100）
    coin_total: float = 0.0
    coin_limit_hit: bool = False
    wheel_spins: int = 0            # 轉盤（每天 10 次）
    wheel_total: float = 0.0
    wheel_done: bool = False
    watch_total: float = 0.0        # 在面板領到的觀看任務蝦幣
    watch_minutes: int = 0          # 面板上的觀看進度（滿 90 分鐘）
    watch_done: bool = False
    withdrawals: int = 0            # 提領次數（每天 3 次）
    withdrawn: float = 0.0
    pending: float = 0.0            # 最後一次看到的待提領蝦幣數
    last_panel: float = 0.0         # 最後一次開面板的時間（epoch）
    final_panel: bool = False       # 今天收尾的面板操作做過了沒
    rooms: dict = field(default_factory=dict)

    def room(self, key: str) -> dict:
        return self.rooms.setdefault(key, {"claims": 0, "coins": 0.0, "done": False,
                                           "wheel": False, "watched": False})

    def room_done(self, key: str) -> bool:
        return bool(key) and self.rooms.get(key, {}).get("done", False)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._day = self._load()

    def _load(self) -> Day:
        today = date.today().isoformat()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                names = {f.name for f in dataclasses.fields(Day)}
                return Day(**{k: v for k, v in data.items() if k in names})
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("讀取 %s 失敗，從頭開始", self.path)
        return Day(date=today)

    @property
    def day(self) -> Day:
        today = date.today().isoformat()
        if self._day.date != today:
            log.info("換日了：%s", self.summary())
            self._day = Day(date=today)
            self.save()
        return self._day

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dataclasses.asdict(self._day), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def summary(self) -> str:
        d = self._day
        return (f"{d.date} 直播間蝦幣 {d.coin_claims} 次 +{d.coin_total:.2f}｜"
                f"轉盤 {d.wheel_spins} 次 +{d.wheel_total:.2f}｜"
                f"觀看任務 {d.watch_minutes} 分鐘 +{d.watch_total:.2f}｜"
                f"提領 {d.withdrawals} 次 {d.withdrawn:.0f}｜待提領 {d.pending:.2f}")
