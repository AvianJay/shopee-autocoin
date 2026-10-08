# 蝦皮直播自動領蝦幣：研究筆記

研究日期：2026-10-08。環境：OPPO CPH2251（Android 13，1080x2400）、蝦皮 TW 3.81.32。

## 環境 / 工具

- `adb -P 5037 -s 100.112.173.34:38841`。使用者環境的 `ANDROID_ADB_SERVER_PORT=5137`，一定要指定 5037。
- 原生 `uiautomator dump` 在蝦皮會失敗（could not get idle state）。
- Python `uiautomator2`（3.7，只推 jar，不裝 APK）的 `d.dump_hierarchy()` 約 1～2 秒，可正常讀取。
- uiautomator2 的程序結束時，會把「自己啟動的」server 一起關掉。所以要用單一長駐程序，不要多個短命程序交錯呼叫。
- 直播相關 UI 都在原生 plugin `com.shopee.tw.dfpluginshopee7`，resource-id 前綴是 `com.shopee.tw.dfpluginshopee7:id/`（下文省略）。
- 任務獎勵面板、「看更多」列表是 React Native，沒有 resource-id，只能靠 text 或 RN testID（會出現在 resource-id 欄位，例如 `live_card_cover_0`）定位。

## 進入直播

1. 首頁底部 `content-desc=tab_bar_button_video_and_live`（直播短影音）
2. 上方 `content-desc="click 1 tab"`（直播）。此時是預覽模式，會顯示「向上滑動以查看完整內容」。
3. 往上滑（如 `swipe 540,1800 → 540,700`）進入完整直播間（HomeActivity 內，`live_cubepage_0` 是 ViewPager，上下滑會換台）。
4. 直播間右上 `explore_layout`（文字「看更多」或「關注中」）會開啟 RN 列表 `ReactTransparentActivity`：
   - 每張卡片 `resource-id=live_card_cover_N`，子文字包含觀看數、`最多X`（該場可領蝦幣上限）／`抽獎`／`優惠券`、標題、直播主名稱。
   - 可一直往下捲，實測 61 間裡有 36 間標「最多X」（0.06～2.4）、6 間標「抽獎」。
   - 點卡片會開 `LiveStreamingAudienceActivityV2`（獨立直播間，右上 `iv_page_close` 關閉後回列表；列表左側 `collapse_icon` 收起）。
   - 列表一打開可能有廣告彈窗：一個大的可點 ViewGroup 加一個 96x96 的關閉鈕，都沒有 text 或 id，只能靠幾何特徵辨識。

## 直播間獎勵元件（都在 `flexbox` 底下）

| 元件 | 節點 | 說明 |
|---|---|---|
| 任務獎勵框 | `box_view_reward_container`，標題圖 `iv_banner` | 點 `iv_banner` 會開任務獎勵面板 |
| 觀看直播任務 | `box_view_watch_live_task` → `tv_title`=觀看直播任務、`tv_coin_num_0`（泡泡，未領取的累計）、`tv_coin_num`（每輪 0.01）、`tv_state_btn`（倒數 mm:ss / 領取） | 每 60～90 秒一輪，到點顯示「領取」，約 6 秒內**自動**累積進泡泡 |
| 直播間蝦幣（賣家） | `box_view_coin_streamer` → `tv_title`=直播間蝦幣、`tv_coin_num`（本輪金額）、`tv_state_btn`（倒數 / 「領取」） | 倒數到 0 變「領取」，**要點擊**；也有 `box_view_coin_streamer_v2` 變體 |
| 幸運轉盤 | `box_view_lucky_draw` → `tv_state_btn`=參加／已參加／已結束 | 見下方 |
| 寵粉紅包雨 | `box_view_luck_rain`（內含 `tv_count_down`/`stv_count_down`） | 尚未遇到實例 |
| 蝦皮紅包雨 | `box_view_super_rain`（同上） | 尚未遇到實例 |
| 蝦幣限時領 | `box_view_ad` → `adview` | 只是廣告導流到別的直播間，**不要點**（點了會離開目前直播間） |

### 直播間蝦幣領取

- 點 `box_view_coin_streamer` 裡的 `tv_state_btn`（文字=領取）。
- 約 1～2 秒內出現 `live_reward_task_toast_view`：`tv_coin_claimed_content`=「成功領取 0.35 Coins」、`tv_coin_claimed_sub_content`=「已發放至「待提領蝦幣數」中」。
- 接著換下一輪：金額可能不同（0.35 → 0.4），倒數重新開始（實測第 2 輪約 9 分鐘）。
- **離開直播間（換到別間或被廣告導走）倒數會重置**；開任務面板、提領頁（直播縮成浮窗）不影響倒數。
- 浮窗 `rl_float_window` 的關閉鈕 `small_iv_close` 會關掉直播，不能誤點。

### 幸運轉盤

1. 點 `box_view_lucky_draw`，開出轉盤（`draw_title`=幸運轉盤，容器 `main_play_layout`）。
2. 「開始」沒有獨立節點，要點轉盤中心（實測 540,1101）。
3. 沒關注的話會跳 `tv_title`=僅限粉絲！、`tv_confirm`=關注，按關注後要**再按一次開始**。
4. 轉約 5～8 秒後出現結果：`fl_lucky_result`，`tv_result`=「你獲得 0.01 蝦幣！」，`tv_more`=「今天還有 9 次的抽獎機會。」。
5. 按 `ic_close` 關閉。
- 每間只能轉一次（再開會直接顯示上次結果），一天 10 次。獎項為 0.01～0.1，大獎 16。

## 任務獎勵面板（`com.shopee.app.react.ReactActivity_`，RN）

- 「待提領蝦幣數」數字 + `提領` 按鈕。
- 觀看直播任務：「觀看直播 90 分鐘 以獲得 蝦幣 或 優惠券」，有進度條（N分鐘/90分鐘），90 分鐘可「抽999蝦幣」。累計的泡泡蝦幣要在這裡按 `領取`，按完變「觀看」，「待提領」立刻增加。
- 直播間蝦幣活動：`觀看` 按鈕（從直播間進來時只是返回原直播間）。
- 注意事項原文重點：
  - 官方直播任務須「當日」領取；非官方（賣家）須於關播前領取，逾期視為放棄。
  - 每天只能提領三次，提領金額只能是固定值。

### 提領頁

- 金額選項：1.00／5.00／10.00／15.00／20.00／25.00／30.00／35.00／40.00／50.00，預設不選。
- 按 `立即提領` 送出。頁面上有「您每天至多只能提領三次」、「待提領蝦幣數: X」。
- 直播浮窗會蓋住部分選項（40.00）。

## 官方規則（help.shopee.tw 文章 169078 / 145413）

- 賣家獎勵（直播間蝦幣）：每場最多 6 次，間隔依序 6/7/8/9/10/10 分鐘；每人每天最多 100 次；重新開播重算。
- 官方獎勵（觀看直播任務）：每場最多累計 10 分鐘；不佔 100 次額度。
- 只能在單一裝置領取。

## App 內訊息（APK zh-rTW 字串，可用來判斷狀態）

- 直播間蝦幣：`您獲得 %s 蝦幣!`、`領取失敗，請再試一次`、`本場直播還可領取 %s 次直播蝦幣`、`已超過今天領取的次數限制，請明天再來`、`您已達領取上限`、`直播蝦幣已全部發放。`、`您已達多裝置使用上限`、`已達裝置數限制`、`限定`
- 觀看任務：`明天再來`（今日已完成）、`抱歉，您不符合領取此獎勵資格`（字串名含 AFfailed＝風控）、`領取蝦幣失敗，請稍後再試`、`您已經領取了這個任務的獎勵`
- 轉盤：`僅限粉絲！`、`現在就關注主播！`、`達到每日參與上限`、`達到每日上限，請明天再回來！`、`獎品已被領取完畢。`、`系統忙於發放蝦幣，請晚點再檢查你的蝦幣。`
- 紅包雨：`寵粉紅包雨`（Lucky Rain）、`蝦皮紅包雨`（Super Rain）
- 其他：`觀看人數已達到上限，請稍後再試。`、`您已達到關注的上限數量！`

## 風險

- 蝦皮服務條款 6.2(k) 禁止以機器人或類似軟體使用平台；官方說明頁列出「偵測到風險活動」時會拒絕領取，或讓領取按鈕消失。
- ADB／開發者選項、UiAutomation（uiautomator2）、無障礙服務，對 App 而言都是可被察覺的狀態。

## 實作時踩到的坑（2026-10-08）

- **直播間蝦幣領完的瞬間**：成功提示出來時按鈕還會停在「領取」1～2 秒，這時再點同一個位置，會打開任務獎勵面板（點到 `box_view_reward_container`）。要等按鈕換成倒數再繼續。
- 同一場領了幾次之後，`box_view_coin_streamer` 會整個消失（健康家這場領 3 次就沒了），所以消失超過 60 秒就當作這場沒了。
- **轉盤**：
  - 按「僅限粉絲」的「關注」時就會直接開轉，結果要重開轉盤才看得到（顯示上次結果）。
  - 圖示上的「已參加」只代表已關注，**不代表轉過**，要打開看是「開始」還是結果。
  - 關注對話框關掉後，它的空殼視窗（沒有子節點，bounds 跟對話框一樣）會擋一陣子。這段時間 `getWindows()` 讀不到底下的轉盤和直播間，只看得到空殼，因為被模態視窗蓋住的視窗不算可互動。
- 電腦版用 TaskStop 之類強制結束 Python 時，手機上的 uiautomator2 server（`app_process … com.wetest.uia2.Main`）不會跟著關掉，要用 `pkill -f uiautomator` 收掉。UiAutomation 連著時可能會壓制其他無障礙服務。
- ColorOS 不讓 adb 執行 `settings put secure`（缺 `WRITE_SECURE_SETTINGS`），所以手機版的無障礙服務要手動開。
- 第一次 Gradle 編譯偶爾會遇到 `FileLockContentionHandler … BindException`，重試就好。

## 尚未確認

- 紅包雨的實際玩法（需要遇到實例；程式碰到時會存畫面到 `logs/captures/rain-*`）。
- 達到每日 100 次上限時的實際畫面。
- 提領超過每天 3 次時的提示文字（目前用「上限」「已…次…提領」比對）。
