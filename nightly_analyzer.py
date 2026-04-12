#!/usr/bin/env python3
"""
夜間自動分析（零 Claude token）
================================
每天凌晨 02:30 跑，分析過去 24 小時資料，產生改善建議報告

不會打 LLM API，純 Python 統計分析
報告寫到 data/nightly_report.md，下次有 Claude session 時讀取執行

執行：
    python3 nightly_analyzer.py
    python3 nightly_analyzer.py --days 7  # 分析 7 天
"""
import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
LAUNCHD_LOG = DATA_DIR / "launchd.log"
SCRAPER_LOG = DATA_DIR / "scraper.log"
WATCHLIST_FILE = DATA_DIR / "sku_watchlist.json"
STATE_FILE = DATA_DIR / "cdn_state.json"
SCRAPED_FILE = DATA_DIR / "scraped_skus.json"
PREDICTED_FILE = DATA_DIR / "predicted_skus.json"
HISTORY_FILE = DATA_DIR / "auto_buy_history.json"
REPORT_FILE = DATA_DIR / "nightly_report.md"
METRICS_FILE = DATA_DIR / "nightly_metrics.json"  # 累積長期 metrics

LOG_LINE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+)")
SCAN_DONE = re.compile(r"掃描完成：CDN 上有 (\d+) 個 SKU，新警報 (\d+) 則")
SCAN_START = re.compile(r"開始掃描 (\d+) 個 SKU")
NEW_SKU = re.compile(r"🚨 新發現！SKU: (\w+)")


def parse_log(path: Path, since: datetime) -> list:
    """解析 log 拿出 (timestamp, msg) tuples，僅 since 之後"""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= since:
            out.append((ts, m.group(2)))
    return out


def analyze_scans(events: list) -> dict:
    """從 events 統計掃描表現"""
    scan_starts = []
    scan_dones = []
    new_skus = []
    new_alerts = 0

    for ts, msg in events:
        if "開始掃描" in msg:
            scan_starts.append(ts)
        if SCAN_DONE.search(msg):
            m = SCAN_DONE.search(msg)
            cdn_count = int(m.group(1))
            alerts = int(m.group(2))
            scan_dones.append((ts, cdn_count, alerts))
            new_alerts += alerts
        if NEW_SKU.search(msg):
            m = NEW_SKU.search(msg)
            new_skus.append((ts, m.group(1)))

    # 計算掃描間隔
    intervals = []
    for i in range(1, len(scan_starts)):
        delta = (scan_starts[i] - scan_starts[i-1]).total_seconds()
        if delta < 600:  # 排除 launchd 重啟空隙
            intervals.append(delta)

    # 計算每次掃描耗時（start → done 配對）
    durations = []
    for i in range(min(len(scan_starts), len(scan_dones))):
        d = (scan_dones[i][0] - scan_starts[i]).total_seconds()
        if 0 <= d < 60:
            durations.append(d)

    # CDN 命中數變化
    cdn_counts = [c for _, c, _ in scan_dones]

    return {
        "scan_count": len(scan_starts),
        "interval_avg": statistics.mean(intervals) if intervals else 0,
        "interval_max": max(intervals) if intervals else 0,
        "duration_avg": statistics.mean(durations) if durations else 0,
        "duration_max": max(durations) if durations else 0,
        "cdn_count_min": min(cdn_counts) if cdn_counts else 0,
        "cdn_count_max": max(cdn_counts) if cdn_counts else 0,
        "cdn_count_last": cdn_counts[-1] if cdn_counts else 0,
        "new_alerts_total": new_alerts,
        # 為了 JSON 序列化把 datetime 轉 str
        "new_skus": [(ts.isoformat(), sku) for ts, sku in new_skus],
    }


def analyze_drop_times(events: list, days: int = 7) -> dict:
    """統計新 SKU 出現的星期幾 + 時段分布"""
    weekday_count = Counter()  # 0=Mon
    hour_count = Counter()
    weekday_hour = Counter()

    for ts, msg in events:
        if NEW_SKU.search(msg):
            weekday_count[ts.weekday()] += 1
            hour_count[ts.hour] += 1
            weekday_hour[(ts.weekday(), ts.hour)] += 1

    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    return {
        "by_weekday": {weekday_names[k]: v for k, v in sorted(weekday_count.items())},
        "by_hour": {f"{h:02d}:00": v for h, v in sorted(hour_count.items())},
        "top_3_drop_windows": [
            (weekday_names[wd], f"{h:02d}:00", n)
            for (wd, h), n in weekday_hour.most_common(3)
        ],
    }


def analyze_scraping(since: datetime) -> dict:
    """統計爬蟲表現（從 SCRAPER_LOG）"""
    events = parse_log(SCRAPER_LOG, since)
    runs = 0
    last_count = None
    for ts, msg in events:
        m = re.search(r"watchlist:\s*(\d+)\s*→\s*(\d+)\s*\(\+(\d+)\)", msg)
        if m:
            runs += 1
            last_count = int(m.group(2))
    return {
        "runs": runs,
        "last_watchlist_size": last_count,
    }


def load_metrics_history() -> list:
    if METRICS_FILE.exists():
        try:
            return json.loads(METRICS_FILE.read_text())
        except Exception:
            return []
    return []


def save_metrics_history(history: list):
    """只保留最近 30 天"""
    cutoff = datetime.now() - timedelta(days=30)
    filtered = [m for m in history if datetime.fromisoformat(m["date"]) > cutoff]
    METRICS_FILE.write_text(json.dumps(filtered, ensure_ascii=False, indent=2))


def detect_anomalies(scans: dict, history: list) -> list:
    """偵測異常並產生警示"""
    warnings = []

    if scans["interval_avg"] > 20:
        warnings.append(f"⚠️ 平均掃描間隔 {scans['interval_avg']:.1f}s（預期 15s）")
    if scans["duration_max"] > 30:
        warnings.append(f"⚠️ 單次掃描最久 {scans['duration_max']:.1f}s（預期 < 15s）")
    if scans["cdn_count_min"] / max(scans["cdn_count_max"], 1) < 0.5:
        warnings.append(
            f"⚠️ CDN 命中數波動大 {scans['cdn_count_min']}~{scans['cdn_count_max']}（可能 IP 被擋或商品下架）"
        )

    # 跟昨天比
    if history:
        last = history[-1].get("scans", {})
        if last.get("cdn_count_last") and scans["cdn_count_last"] < last["cdn_count_last"] * 0.9:
            warnings.append(
                f"⚠️ CDN 命中數比昨天少 10%+（昨 {last['cdn_count_last']} → 今 {scans['cdn_count_last']}）"
            )

    return warnings


def generate_recommendations(scans: dict, drops: dict, history: list) -> list:
    """產生具體改善建議"""
    recs = []

    # Drop time 推測
    if drops["top_3_drop_windows"]:
        wd, hr, n = drops["top_3_drop_windows"][0]
        if n >= 3:
            recs.append(f"📅 觀察到 {wd} {hr} 是 drop 高峰（{n} 次），建議該時段提前登入")

    # 趨勢
    if len(history) >= 7:
        recent_alerts = sum(h.get("scans", {}).get("new_alerts_total", 0) for h in history[-7:])
        if recent_alerts == 0:
            recs.append("💤 過去 7 天 0 次新品偵測，watchlist 範圍可能太窄，建議重跑 sku_predictor.py")
        elif recent_alerts > 50:
            recs.append("🔔 過去 7 天大量新品（{}），考慮分流通知避免 spam".format(recent_alerts))

    # 效率
    if scans["scan_count"] > 0 and scans["new_alerts_total"] == 0:
        recs.append(f"📊 過去 24h 掃了 {scans['scan_count']} 次 0 命中，正常 standby")

    return recs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="分析範圍（天）")
    args = parser.parse_args()

    now = datetime.now()
    since = now - timedelta(days=args.days)

    events = parse_log(LAUNCHD_LOG, since)
    scans = analyze_scans(events)
    drops = analyze_drop_times(events, days=args.days)
    scraping = analyze_scraping(since)

    history = load_metrics_history()
    warnings = detect_anomalies(scans, history)
    recs = generate_recommendations(scans, drops, history)

    # 寫累積 metrics
    history.append({
        "date": now.isoformat(),
        "scans": scans,
        "drops": drops,
        "scraping": scraping,
    })
    save_metrics_history(history)

    # 寫報告
    watchlist_size = len(json.loads(WATCHLIST_FILE.read_text())) if WATCHLIST_FILE.exists() else 0
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    notified_size = len(state.get("notified", []))

    report = f"""# Hermes Monitor — 夜間分析報告

> 產生時間：{now.strftime("%Y-%m-%d %H:%M")}
> 分析範圍：過去 {args.days} 天（since {since.strftime("%Y-%m-%d %H:%M")}）

## 系統現況

- Watchlist: **{watchlist_size}** 個 SKU
- Notified（已靜音）: **{notified_size}** 個
- CDN 上目前存在: **{scans['cdn_count_last']}** 個

## 掃描表現

| 指標 | 數值 |
|---|---|
| 掃描次數 | {scans['scan_count']} |
| 平均間隔 | {scans['interval_avg']:.1f}s |
| 最長間隔 | {scans['interval_max']:.1f}s |
| 平均耗時 | {scans['duration_avg']:.1f}s |
| 最長耗時 | {scans['duration_max']:.1f}s |
| CDN 命中範圍 | {scans['cdn_count_min']}~{scans['cdn_count_max']} |
| **新警報數** | **{scans['new_alerts_total']}** |

## 新品偵測

"""
    if scans["new_skus"]:
        report += "今日偵測到的新 SKU：\n\n"
        for ts_str, sku in scans["new_skus"]:
            ts_short = ts_str[11:19] if len(ts_str) >= 19 else ts_str
            report += f"- `{sku}` @ {ts_short}\n"
    else:
        report += "_本期間無新品偵測_\n"

    report += f"""
## Drop time 分布（{args.days} 天）

### 星期分布
"""
    for wd, n in drops["by_weekday"].items():
        bar = "█" * min(n, 20)
        report += f"- {wd}: {bar} ({n})\n"

    if drops["top_3_drop_windows"]:
        report += "\n### Top 3 drop 時段\n"
        for i, (wd, hr, n) in enumerate(drops["top_3_drop_windows"], 1):
            report += f"{i}. {wd} {hr} ({n} 次)\n"

    report += f"""
## 爬蟲表現

- 過去 {args.days} 天執行: {scraping['runs']} 次
- 最新 watchlist 大小: {scraping['last_watchlist_size'] or '無變化'}

"""

    if warnings:
        report += "## ⚠️ 異常警示\n\n"
        for w in warnings:
            report += f"- {w}\n"
        report += "\n"

    if recs:
        report += "## 💡 改善建議\n\n"
        for r in recs:
            report += f"- {r}\n"
        report += "\n"

    report += """## 下一步

下次有 Claude session 時，先讀這個檔案 + IMPROVEMENT_BACKLOG.md，挑高 ROI 項目執行。

---

_本報告由 nightly_analyzer.py 自動產生（純 Python，零 LLM token）_
"""

    REPORT_FILE.write_text(report, encoding="utf-8")
    print(f"✅ 報告寫到 {REPORT_FILE}")
    print(f"✅ Metrics 寫到 {METRICS_FILE}")
    print(f"\n=== 摘要 ===")
    print(f"掃描 {scans['scan_count']} 次，新警報 {scans['new_alerts_total']} 則")
    if warnings:
        print(f"⚠️ {len(warnings)} 個異常")
    if recs:
        print(f"💡 {len(recs)} 個建議")


if __name__ == "__main__":
    main()
