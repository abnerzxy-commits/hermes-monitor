# Hermes Monitor — 改善 backlog

> 競品研究日期：2026-04-08
> 每次工作前先讀這個檔案，挑最高 ROI 的項目做。做完打勾並記日期。

## 競品快照（2026-04 資料）

| 服務 | 強項 | 弱點 | 月費 |
|---|---|---|---|
| Restock Checker | Hermes 專用 | hermes.com 直爬，會被擋 | $5~15 |
| Hermès Stock Monitor app | 桌面 desktop notification | scroll 全站慢 | $? |
| BagUSeek | 監控 32 國 | 「within minutes」慢 | $? |
| Visualping | 通用 | 要手動指定 URL | $5+ |
| **我的系統** | CDN 探測 + ML 預測 + 15 秒 + 免費 | 只有 TW + 沒桌面通知 | $0 |

## 我贏的地方（保持優勢）

- ✅ CDN 探測（assets.hermes.com 沒 DataDome）
- ✅ 15 秒輪詢（比競品「分鐘級」快 4-20 倍）
- ✅ ML SKU 預測（自動發現未上架商品，命中 12.8%）
- ✅ 自動爬蟲 nightly（不用手動加 URL）
- ✅ 完全免費

## 我輸的地方（待補）

### 🟢 高 ROI 低成本（優先做）

- [x] ~~**多國監控**~~：2026-04-09 完成。爬 TW/US/FR/JP 4 區，watchlist 386 → 936（**+550**）。FR 用法文路徑 `maroquinerie/*`
- [ ] **drop time 統計**：分析 launchd.log 找出 SKU 第一次出現的「星期幾 + 時段」分布。Hermes 常在週二/週四美東早上 drop。記下來給用戶提醒
  - 預估工作量：30 分鐘
  - 預估效益：用戶能在 drop time 前先準備好登入
  - **資料夠了再做**（至少 7 天 launchd.log）
- [x] ~~**桌面 notification**~~：2026-04-08 完成（osascript + Glass 音效）
- [x] ~~**星標收藏 SKU 優先警報**~~：2026-04-09 完成。⭐ 邏輯：「不在 known_before → 現在存在」才警報，4h 復警報冷卻
- [x] ~~**HTTP keep-alive**~~：2026-04-09 完成。用 `requests.Session` thread-local pool，936 SKU 35s → **5s**（7 倍快）

### 🟡 中 ROI 中成本

- [ ] **detection 速度自動調節**：歷史上沒新品的時段降到 30s，drop window（週二/週四 21:00-23:00 TW）升到 5s
  - 預估工作量：1 小時
  - 預估效益：平均成本下降 50% + drop window 速度 ×3
- [ ] **TG bot 通知 fallback**：LINE 偶爾延遲，TG 通常 < 1 秒
  - 預估工作量：30 分鐘
  - 預估效益：通知絕對不會漏
- [ ] **Hermes.com session keep-alive**：每 30 分鐘自動 ping hermes.com 保持 cookies fresh，避免 auto_buy 時要重登
  - 預估工作量：20 分鐘
- [ ] **錯誤率監控**：CDN HEAD 失敗超過 5% 時 LINE 通知（防止 IP 被悄悄擋）
  - 預估工作量：30 分鐘

### 🔴 低 ROI 或高成本（不急）

- [ ] 自動結帳（DataDome 太硬，且需 OTP，不可能完成）
- [ ] 多帳號平行加購（要多帳號 + 多 IP）
- [ ] 機器學習 drop 預測（資料不夠）

## 已完成

- [x] 2026-04-08: CDN 早期警報系統（5min cron）
- [x] 2026-04-08: auto_buy 瀏覽器自動加購（14.2 秒成功）
- [x] 2026-04-08: 升級 15 秒 launchd 輪詢
- [x] 2026-04-08: 擴大監控 SKU 2 → 386（爬蟲 + ML 預測，命中 12.8%）
- [x] 2026-04-08: enrichment（商品名 + 顏色塞進通知）
- [x] 2026-04-08: race condition 修復（read-modify-write）
- [x] 2026-04-08: nightly scraper launchd（每天 03:00）
- [x] 2026-04-08: nightly_analyzer.py（純 Python，零 token，每天 02:30）
- [x] 2026-04-08: 桌面 notification（osascript）
- [x] 2026-04-09: **多國監控** TW/US/FR/JP 4 區，watchlist 386 → 936（+143%）
- [x] 2026-04-09: **HTTP keep-alive** session pool，掃描速度 35s → 5s（7 倍）
- [x] 2026-04-09: **星標收藏 SKU**（⭐ 永遠通知，4h 復警報冷卻）

## 工作原則

1. **零 token 自動化優先**：能用純 Python 排程的就不要用 Claude 跑
2. **不增加月費**：所有解都要免費
3. **不被擋**：rate limit 寬鬆，遠離 DataDome
4. **觀察期 1 週**：每個改動觀察 7 天再決定保留
