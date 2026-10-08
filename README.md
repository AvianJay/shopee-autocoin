# 蝦皮直播自動領蝦幣

在蝦皮直播間自動領取：

- **直播間蝦幣**：倒數到了就按「領取」。
- **幸運轉盤**：限粉絲的會自動關注。
- **觀看直播任務**：定時開任務面板，把累積的蝦幣領進「待提領」。
- **提領**（可關閉）：把「待提領」轉進「我的蝦幣」。

有兩個版本，邏輯相同：

| | 電腦版 | 手機版 |
|---|---|---|
| 位置 | `autocoin/`（Python） | `android/`（Kotlin App「蝦幣助手」） |
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
4. 每 30 分鐘開一次任務獎勵面板，領觀看任務的蝦幣。23:40 後一定會再開一次，因為這筆要當天領。
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

## 手機版（蝦幣助手）

```powershell
cd D:\proj\shopee-autocoin\android
.\gradlew.bat assembleDebug
adb -P 5037 install -r app\build\outputs\apk\debug\app-debug.apk
```

1. 開啟無障礙服務：設定 → 其他設定 → 無障礙 → 蝦幣助手 → 開啟。也可以在 App 裡按「開啟無障礙設定」。
2. 開始的方式有兩種：
   - 打開 App 按「開始」。
   - 點畫面最下面的小黑條「蝦幣 ■ 已停止」。
3. App 會自己打開蝦皮開始領。要用手機時，點小黑條或在 App 裡按「停止」。
4. 「自動提領」開關在 App 主畫面，關掉就只領不提領。
5. 執行中，小黑條會讓螢幕保持亮著。如果手機被鎖住，程式會等你解鎖再繼續。

## 風險

蝦皮服務條款 6.2(k) 禁止用機器人等軟體使用平台。官方說明也寫了，偵測到風險時會拒絕領取或讓領取按鈕消失。使用前請自行評估。
