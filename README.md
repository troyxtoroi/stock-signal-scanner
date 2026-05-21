# 台股信號偵測器

每天台灣收盤後自動掃描股票買入信號，找到機會就推播通知你。

## 功能

- 均線黃金交叉偵測（5日線穿越20日線）
- 成交量異常放大偵測（超過均量2倍）
- RSI 超賣反彈偵測（RSI 從30以下回升）
- 高殖利率偵測（超過5%）
- 每天台股收盤後（15:30）自動執行
- 推播通知到手機或電腦（用 ntfy.sh）

## 設定觀察清單

編輯 `config.json`，修改 `watchlist` 加入你想追蹤的股票。
台股格式：股票代碼 + `.TW`，例如台積電是 `2330.TW`。

## 設定通知

1. 手機安裝 [ntfy app](https://ntfy.sh)（Android / iOS 都有）
2. 訂閱頻道名稱：`stock-signals-troychao`
3. 之後有信號就會自動推播到你手機

如果想改成自己的私人頻道名稱，在 `config.json` 修改 `ntfy_topic`。

## 部署到 GitHub Actions

1. 在 GitHub 建立新的 repo
2. 把這個資料夾的所有檔案推上去
3. GitHub Actions 會自動每天執行（週一到週五）
4. 也可以在 GitHub 網站手動點「Run workflow」測試
