"""手機連線（ADB + uiautomator2）與基本操作。"""
from __future__ import annotations

import logging
import time

import adbutils
import uiautomator2 as u2

from .ui import PKG, Node, Snapshot

log = logging.getLogger(__name__)


class Device:
    def __init__(self, serial: str, host: str = "127.0.0.1", port: int = 5037):
        self.serial = serial
        # 不用 adbutils 預設的 client：它讀 ANDROID_ADB_SERVER_PORT，使用者環境裡是 5137
        self.adb = adbutils.AdbClient(host=host, port=port)
        self.d: u2.Device | None = None
        self.connect()

    def connect(self):
        if ":" in self.serial:  # 無線偵錯
            try:
                self.adb.connect(self.serial, timeout=10)
            except Exception as e:
                log.warning("adb connect %s 失敗：%s", self.serial, e)
        self.d = u2.connect(self.adb.device(self.serial))

    def _retry(self, fn):
        """連線偶爾會斷（Tailscale、uiautomator server 重啟），重連後再試。"""
        for attempt in range(3):
            try:
                return fn()
            except Exception as e:
                if attempt == 2:
                    raise
                log.warning("裝置操作失敗（%s: %s），重新連線後重試", type(e).__name__, e)
                time.sleep(3 * (attempt + 1))
                try:
                    self.connect()
                except Exception as e2:
                    log.warning("重新連線失敗：%s", e2)

    def shell(self, cmd: str) -> str:
        return self._retry(lambda: self.adb.device(self.serial).shell(cmd))

    # ---------- 讀取 ----------
    def snapshot(self) -> Snapshot:
        xml = self._retry(lambda: self.d.dump_hierarchy())
        cur = self.current()
        return Snapshot(xml, cur.get("package", ""), cur.get("activity", ""))

    def current(self) -> dict:
        try:
            return self._retry(lambda: self.d.app_current())
        except Exception:
            return {}

    def screen_on(self) -> bool:
        return "mWakefulness=Awake" in self.shell("dumpsys power | grep mWakefulness=")

    def locked(self) -> bool:
        return "isKeyguardShowing=true" in self.shell("dumpsys window | grep isKeyguardShowing")

    def home_package(self) -> str:
        out = self.shell("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                         "-c android.intent.category.HOME").strip().splitlines()
        return out[-1].split("/")[0] if out else ""

    def last_toast(self) -> str:
        try:
            return self._retry(lambda: self.d.last_toast) or ""
        except Exception:
            return ""

    def clear_toast(self):
        try:
            self._retry(lambda: self.d.clear_toast())
        except Exception:
            pass

    def screenshot(self, path: str):
        self._retry(lambda: self.d.screenshot(path))

    # ---------- 操作 ----------
    def tap(self, x: int, y: int):
        self._retry(lambda: self.d.click(x, y))

    def tap_node(self, node: Node):
        self.tap(*node.center)

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float = 0.4):
        self._retry(lambda: self.d.swipe(fx, fy, tx, ty, duration=duration))

    def back(self):
        self._retry(lambda: self.d.press("back"))

    def wake(self):
        self._retry(lambda: self.d.screen_on())

    def launch(self):
        self._retry(lambda: self.d.app_start(PKG))

    def restart_app(self):
        self._retry(lambda: self.d.app_start(PKG, stop=True))
