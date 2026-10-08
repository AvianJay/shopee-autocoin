# 蝦皮直播自動領蝦幣

在蝦皮直播間自動領取：

- **直播間蝦幣**：倒數到了就按「領取」。
- **幸運轉盤**：限粉絲的會自動關注。
- **觀看直播任務**：定時開任務面板，把累積的蝦幣領進「待提領」。
- **提領**（可關閉）：把「待提領」轉進「我的蝦幣」。

有兩個版本，邏輯相同：

| | 電腦版 | 手機版 |
|---|---|---|
| 位置 | `autocoin/`（Python） | `android/`（Kotlin App「畫面輔助」） |
| 怎麼控制手機 | ADB + uiautomator2 | 手機上的無障礙服務 |
| 需要電腦開著 | 要 | 不用 |

研究過程與畫面元件的細節在 [RESEARCH.md](RESEARCH.md)。

## 它會做什麼

1. 打開直播間右上角的「看更多」列表，挑一間直播間進去，優先順序如下：
   - 轉盤次數還有剩：先挑標「抽獎」的。
   - 否則挑卡片上「最多X」最高的。
2. 待在那間：
   - 倒數到了就領，一直領到這場沒有直播間蝦幣為止。
   - 中途**不會離開**，因為離開會讓倒數重來。
3. 有轉盤就轉。每間一次，一天 10 次。
4. 每 30 分鐘開一次任務獎勵面板（間隔有 ±25% 抖動），領觀看任務的蝦幣。23:35~23:50 之間會再開一次，因為這筆要當天領。
5. 提領（可關閉）的條件：
   - 待提領 ≥ 5 時，選不超過餘額的最大面額。
   - 收尾時只要剩 ≥ 1 也會提領。
   - **每天最多 3 次**。如果你手動提領過，蝦皮回報上限時程式也會停止提領。
6. 直播間蝦幣用完當天的 100 次、轉盤也用完時，改成每間待 10 分鐘累積觀看任務（每天 90 分鐘）。全部做完就休息到隔天。
7. 蝦皮回報「不符合領取此獎勵資格」「偵測到風險活動」「已達裝置數限制」時，**立刻停止**。

## 電腦版

```powershell
cd D:\proj\shopee-autocoin
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python -m autocoin run            # 開始（Ctrl+C 停止）
.venv\Scripts\python -m autocoin run --hours 3  # 跑 3 小時
.venv\Scripts\python -m autocoin run --no-withdraw  # 不自動提領
.venv\Scripts\python -m autocoin status         # 今天的統計
.venv\Scripts\python -m autocoin scan           # 列出「看更多」裡的直播間
.venv\Scripts\python -m autocoin panel          # 開一次任務面板（領觀看任務、視情況提領）
```

- 裝置序號和 adb port 的預設值在 `autocoin/config.py`，也可以用 `--serial`、`--adb-port` 指定。程式固定連 5037，不受 `ANDROID_ADB_SERVER_PORT` 影響。
- 紀錄檔在 `logs/autocoin.log`，當天統計在 `state.json`。
- 遇到沒看過的畫面，截圖和 UI 樹會存在 `logs/captures/`。
- 執行中手機會被操作，**不要同時用手機**。
- 建議開啟「充電時保持螢幕常亮」，避免鎖屏中斷。

## 手機版（畫面輔助）

```powershell
cd D:\proj\shopee-autocoin\android
.\gradlew.bat assembleDebug
adb -P 5037 install -r app\build\outputs\apk\debug\app-debug.apk
```

1. 開啟無障礙服務：設定 → 其他設定 → 無障礙 → 畫面輔助 → 開啟。也可以在 App 裡按「開啟無障礙設定」。
2. 開始的方式有兩種：
   - 打開 App 按「開始」。
   - 點畫面最下面的小黑條「蝦幣 ■ 已停止」。
3. App 會自己打開蝦皮開始領。要用手機時，點小黑條或在 App 裡按「停止」。
4. 「自動提領」開關在 App 主畫面，關掉就只領不提領。
5. 執行中，小黑條會讓螢幕保持亮著。如果手機被鎖住，程式會等你解鎖再繼續。

## Nightly 建置（GitHub Actions）

`.github/workflows/nightly.yml` 會在 push 到 `main`、每天台灣時間 02:00、或手動觸發時建 signed release APK，然後**更新同一個 `nightly` release**（固定檔名 `autocoin-nightly.apk`，每次覆蓋，不會愈積愈多）。

需要在 repo 的 Settings → Secrets and variables → Actions 設好這三個：

- `KEYSTORE_BASE64`：`base64 -w0 release.jks` 的輸出
- `KEYSTORE_ALIAS`
- `KEYSTORE_PASSWORD`（同時當 store password 和 key password）

本機沒設這三個環境變數時，`build.gradle.kts` 不會建立 signing config，`assembleDebug` 照常可用。

## 降低機器指紋

風控不只看你裝了什麼，也看**行為的規律性**。所以等待時間與點擊座標都加了隨機抖動：

- 所有等待走 `Jitter.ms()`（Kotlin）/ `doze()`（Python），預設 ±18%。
- 點擊不再落在節點正中央，改成中心 ±9 px 的隨機點；滑動端點也一起位移。
- 每個直播間的上限只往上抽（`Jitter.up()`）：觀看任務的 10 分鐘門檻往下抖會做不完，往上抖只是多待一下。
- 面板間隔 ±25%，收尾時間從固定的 23:40 改成 23:35~23:50 之間每天固定一個值。
- 倒數醒來的提前量從固定 2 秒改成 1~6 秒隨機。

抖動量可調：Python 在 `autocoin/config.py` 的 `jitter` / `tap_jitter_px`，Kotlin 在 `Jitter.kt` 的預設參數。

另外無障礙服務的 `app_name` / `service_description` 刻意寫成中性的「畫面輔助」，因為 `getInstalledAccessibilityServiceList()` 會把這兩個字串連同 package 一起讀走。

## 風險

蝦皮服務條款 6.2(k) 禁止用機器人等軟體使用平台。官方說明也寫了，偵測到風險時會拒絕領取或讓領取按鈕消失。使用前請自行評估。
