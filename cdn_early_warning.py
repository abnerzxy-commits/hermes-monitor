#!/usr/bin/env python3
"""
愛馬仕 CDN 早期警報系統
============================

原理：
- 商品圖會比商品頁早 5-30 分鐘上 CDN
- CDN endpoint (assets.hermes.com) 沒有 DataDome 防護，可直接探測
- SKU 格式：{6 位數}{2 英文}{2 數字}，例如 084948CP89
- 200 = 商品已建檔（即將上架）
- 403 = 不存在

工作流程：
1. 載入已知 SKU 清單（從 products.json + 歷史資料）
2. 同時掃描候選 SKU（猜測未來會用的編號）
3. 每 30 秒探測一次
4. 第一次出現 200 → 立刻 LINE 通知 + 自動加入購物車（如果開啟）

執行：
    python3 cdn_early_warning.py             # 一次掃描
    python3 cdn_early_warning.py --loop      # 持續掃描（每 30 秒）
    python3 cdn_early_warning.py --add-sku 084948CP89   # 加入監控清單
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─── Config ────────────────────────────────────────────
HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

CDN_BASE = "https://assets.hermes.com/is/image/hermesproduct"
CDN_PROBE_URL = f"{CDN_BASE}/{{sku}}_front_wm_1?wid=100&hei=100"

# Files
SKU_WATCH_FILE = DATA_DIR / "sku_watchlist.json"  # 監控的 SKU 清單
CDN_STATE_FILE = DATA_DIR / "cdn_state.json"      # 已通知過的 SKU
LOG_FILE = DATA_DIR / "cdn_warning.log"
CDN_KNOWN_FILE = DATA_DIR / "cdn_known_skus.json"  # CDN 上找到過的 SKU
STARRED_FILE = DATA_DIR / "starred_skus.json"  # ⭐ 星標收藏 SKU（永遠會通知）

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")

SKU_PATTERN = re.compile(r"H?(\d{6}[A-Z]{2}\d{2})", re.IGNORECASE)


# ─── Logging ────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ─── State management ──────────────────────────────────
def load_json(path: Path, default):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_watchlist() -> list[str]:
    return load_json(SKU_WATCH_FILE, [])


def save_watchlist(skus: list[str]):
    save_json(SKU_WATCH_FILE, sorted(set(skus)))


def load_state() -> dict:
    return load_json(CDN_STATE_FILE, {"notified": [], "last_scan": None})


def save_state(state: dict):
    """
    儲存 state，會先 reload 磁碟現況再 merge
    防止其他工具（例如 sku_predictor.py）寫入後被覆蓋（race condition）
    """
    state["last_scan"] = datetime.now().isoformat()
    # Reload + merge: 把磁碟上現有的 notified 也納入
    fresh = load_state()
    fresh_notified = set(fresh.get("notified", []))
    my_notified = set(state.get("notified", []))
    state["notified"] = sorted(fresh_notified | my_notified)
    save_json(CDN_STATE_FILE, state)


def load_known_skus() -> list[str]:
    return load_json(CDN_KNOWN_FILE, [])


def save_known_skus(skus: list[str]):
    save_json(CDN_KNOWN_FILE, sorted(set(skus)))


# ─── 星標收藏 ──────────────────────────────────────────
def load_starred() -> set[str]:
    """載入星標 SKU 清單"""
    return set(load_json(STARRED_FILE, []))


def save_starred(skus: set[str]):
    save_json(STARRED_FILE, sorted(skus))


def add_star(sku: str) -> bool:
    """加星標"""
    sku = sku.upper().strip()
    starred = load_starred()
    if sku in starred:
        return False
    starred.add(sku)
    save_starred(starred)
    return True


def remove_star(sku: str) -> bool:
    """移除星標"""
    sku = sku.upper().strip()
    starred = load_starred()
    if sku not in starred:
        return False
    starred.discard(sku)
    save_starred(starred)
    return True


# ─── Bootstrap from existing data ──────────────────────
def bootstrap_from_products() -> list[str]:
    """從 products.json 提取現有 SKU 加入監控"""
    products_file = DATA_DIR / "products.json"
    if not products_file.exists():
        return []

    products = load_json(products_file, {})
    skus = []
    for prod in products.values():
        url = prod.get("url", "")
        match = SKU_PATTERN.search(url)
        if match:
            skus.append(match.group(1).upper())

    if skus:
        log(f"從 products.json 學到 {len(skus)} 個 SKU")
        existing = load_watchlist()
        merged = sorted(set(existing + skus))
        save_watchlist(merged)
        return merged
    return load_watchlist()


# ─── CDN Probe ─────────────────────────────────────────
# Thread-local session pool（HTTP keep-alive，TCP 重用，比每次新建快 5-10 倍）
import threading
_thread_local = threading.local()


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20, pool_maxsize=50, max_retries=0
        )
        s.mount("https://", adapter)
        _thread_local.session = s
    return _thread_local.session


def probe_sku(sku: str, timeout: int = 6) -> dict:
    """探測單一 SKU 是否在 CDN 上存在（重用 HTTP connection）"""
    url = CDN_PROBE_URL.format(sku=sku)
    try:
        r = _get_session().head(url, timeout=timeout, allow_redirects=False)
        return {
            "sku": sku,
            "exists": r.status_code == 200,
            "status": r.status_code,
            "url": url,
        }
    except Exception as e:
        return {"sku": sku, "exists": False, "status": 0, "error": str(e)[:100]}


def probe_batch(skus: list[str], workers: int = 20) -> list[dict]:
    """並行探測多個 SKU"""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(probe_sku, sku): sku for sku in skus}
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


# ─── LINE Notification ─────────────────────────────────
HERMES_HOME = "https://www.hermes.com/tw/zh/"
HERMES_CART = "https://www.hermes.com/tw/zh/cart/"


def _build_personal_message(sku: str, product_url: str | None, cart_added: bool,
                            attempt: int | None, info: dict | None = None) -> list:
    """私訊給管理員（你）的訊息 — 加購結果已知"""
    info = info or {}
    image_url = info.get("image_url") or f"{CDN_BASE}/{sku}_front_wm_1?wid=800&hei=800"
    title = info.get("title")
    color = info.get("color")
    is_starred = bool(info.get("starred"))
    now_str = datetime.now().strftime('%H:%M:%S')

    name_line = f"🎁 {title}" + (f"（{color}）" if color else "") + "\n" if title else ""
    star_prefix = "⭐⭐⭐ 你的星標款上架！⭐⭐⭐\n" if is_starred else ""

    if cart_added:
        text = (
            f"{star_prefix}"
            f"🎉 加購成功！\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"{name_line}"
            f"📦 SKU: {sku}\n"
            f"🕐 加購時間: {now_str}\n"
            f"🔁 嘗試次數: {attempt or 1}\n\n"
            f"✅ 商品已在你的購物車\n\n"
            f"⚡ 立刻去結帳：\n{HERMES_CART}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏰ 提醒：別人也在搶，盡快結帳！"
        )
    else:
        title_text = "⭐ 星標款上架！" if is_starred else "🚨 新品偵測！"
        text = (
            f"{title_text}\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"{name_line}"
            f"📦 SKU: {sku}\n"
            f"🕐 偵測時間: {now_str}\n\n"
            f"⚡ 系統剛偵測到 CDN 出現新商品圖\n"
            f"   通常 5~30 分鐘後會在官網看到\n\n"
            f"🛒 立刻行動：\n"
        )

    url = product_url or info.get("product_url")
    if url:
        text += f"🔗 商品連結：\n{url}\n"
    else:
        text += f"🔍 搜尋連結：\nhttps://www.hermes.com/tw/zh/search/?s={sku}\n"

    return [
        {"type": "text", "text": text},
        {
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        },
    ]


def _build_broadcast_message(sku: str, product_url: str | None, info: dict | None = None) -> list:
    """廣播給所有好友的訊息 — 緊急通知，催促立刻行動"""
    info = info or {}
    image_url = info.get("image_url") or f"{CDN_BASE}/{sku}_front_wm_1?wid=800&hei=800"
    title = info.get("title")
    color = info.get("color")
    is_starred = bool(info.get("starred"))
    now_str = datetime.now().strftime('%H:%M:%S')
    search_url = f"https://www.hermes.com/tw/zh/search/?s={sku}"

    name_line = f"🎁 {title}" + (f"（{color}）" if color else "") + "\n" if title else ""

    title_emoji = "⭐ 熱門款上架！" if is_starred else "🚨 愛馬仕新品上架！"
    text = (
        f"{title_emoji}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"{name_line}"
        f"📦 SKU: {sku}\n"
        f"🕐 上架時間: {now_str}\n\n"
        f"⚡ 系統剛剛偵測到新品\n"
        f"⏳ 數量有限，建議立刻行動\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📋 動作步驟：\n\n"
        f"① 立刻打開 Hermes 官網\n"
        f"   {HERMES_HOME}\n\n"
        f"② 登入你的帳號\n\n"
        f"③ 點商品連結加入購物車：\n"
    )

    url = product_url or info.get("product_url")
    if url:
        text += f"   {url}\n\n"
    else:
        text += f"   {search_url}\n\n"

    text += (
        f"④ 立刻結帳，不要拖延！\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 提醒：商品不會鎖庫存\n"
        f"   先結帳的人才買得到"
    )

    return [
        {"type": "text", "text": text},
        {
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        },
    ]


def send_personal_alert(sku: str, product_url: str | None = None,
                        cart_added: bool = False, attempt: int | None = None,
                        info: dict | None = None) -> bool:
    """私訊給管理員（你）— 含加購結果 + 商品資訊"""
    if not LINE_TOKEN or not LINE_USER_ID:
        log("⚠️ LINE 私訊設定不完整")
        return False

    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_TOKEN}",
            },
            json={
                "to": LINE_USER_ID,
                "messages": _build_personal_message(sku, product_url, cart_added, attempt, info=info),
            },
            timeout=10,
        )
        if r.status_code == 200:
            log(f"✅ 私訊已送達: {sku} (cart_added={cart_added})")
            return True
        log(f"❌ 私訊失敗 [{r.status_code}]: {r.text[:100]}")
        return False
    except Exception as e:
        log(f"❌ 私訊例外: {e}")
        return False


def send_broadcast_alert(sku: str, product_url: str | None = None,
                         info: dict | None = None) -> bool:
    """廣播給所有好友 — 緊急上架通知 + 商品資訊"""
    if not LINE_TOKEN:
        log("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定")
        return False

    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_TOKEN}",
            },
            json={
                "messages": _build_broadcast_message(sku, product_url, info=info),
            },
            timeout=10,
        )
        if r.status_code == 200:
            log(f"✅ 廣播已送達: {sku}")
            return True
        log(f"❌ 廣播失敗 [{r.status_code}]: {r.text[:100]}")
        return False
    except Exception as e:
        log(f"❌ 廣播例外: {e}")
        return False


def _enrich_safe(sku: str) -> dict | None:
    """嘗試做 product enrichment，失敗也不影響主流程"""
    try:
        from product_enrich import enrich_sku
        info = enrich_sku(sku)
        if info.get("success"):
            log(f"📋 enrichment 成功: {info.get('title')} ({info.get('color')})")
        return info
    except Exception as e:
        log(f"⚠️ enrichment 失敗: {e}")
        return None


def send_macos_notification(sku: str, info: dict | None = None) -> bool:
    """發 macOS 桌面通知（你在電腦前的時候第一時間看到）"""
    import subprocess
    info = info or {}
    title = info.get("title") or "Hermès 新品"
    color = info.get("color") or ""
    subtitle = f"{color} • {sku}" if color else sku
    url = info.get("product_url") or f"https://www.hermes.com/tw/zh/search/?s={sku}"
    body = f"立刻去看：{url}"

    # 用 osascript 顯示 macOS native notification
    safe = lambda s: (s or "").replace('"', '\\"').replace("\n", " ")
    script = (
        f'display notification "{safe(body)}" '
        f'with title "🚨 {safe(title)}" '
        f'subtitle "{safe(subtitle)}" '
        f'sound name "Glass"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            timeout=5, capture_output=True
        )
        log(f"🖥️ 桌面通知已發出: {sku}")
        return True
    except Exception as e:
        log(f"⚠️ 桌面通知失敗: {e}")
        return False


# 保留舊 API 相容性
def send_line_alert(sku: str, product_url: str | None = None, starred: bool = False):
    """舊 API：同時發私訊（不含加購結果）+ 廣播。會嘗試先做 enrichment

    Args:
        starred: 是否為星標款（會用獨立 emoji 標示，且強制發送即使在 notified 集合）
    """
    if not LINE_TOKEN:
        log("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，跳過通知")
        return False

    # 抓商品資訊（best-effort，失敗就用 fallback）
    info = _enrich_safe(sku)
    if starred:
        info = info or {}
        info["starred"] = True

    success_count = 0
    if send_personal_alert(sku, product_url, cart_added=False, info=info):
        success_count += 1
    if send_broadcast_alert(sku, product_url, info=info):
        success_count += 1
    # macOS 桌面通知（不算 success_count，是 bonus channel）
    send_macos_notification(sku, info=info)

    return success_count > 0


# ─── Auto-buy hook ─────────────────────────────────────
def trigger_auto_buy(sku: str):
    """觸發自動加入購物車（如果開啟）"""
    auto_buy_enabled = os.getenv("HERMES_AUTO_BUY", "0") == "1"
    if not auto_buy_enabled:
        return

    try:
        # 動態 import，避免沒裝 playwright 時報錯
        sys.path.insert(0, str(HERE))
        from auto_buy import add_to_cart_with_retry
        log(f"🛒 觸發自動加購: {sku}")
        # 預設用 use_chrome=True（連線到本機 Chrome remote debugging）
        # 比較不會被 DataDome 擋，且有反封鎖速率限制保護
        use_chrome = os.getenv("HERMES_USE_CHROME", "1") == "1"
        # 用重試模式：商品圖出現後 8 分鐘內持續嘗試加購
        result = add_to_cart_with_retry(sku, use_chrome=use_chrome)
        if result.get("success"):
            log(f"✅ 加入購物車成功: {sku}")
            # 發二次通知
            send_line_alert(sku, product_url=result.get("cart_url"))
        elif result.get("blocked"):
            log(f"🛑 被擋了，已自動進入冷卻期")
        elif result.get("rate_limited"):
            log(f"⏳ 速率限制觸發：{result.get('error')}")
        else:
            log(f"❌ 加入購物車失敗: {result.get('error')}")
    except Exception as e:
        log(f"❌ 自動加購例外: {e}")


# ─── Main scan logic ───────────────────────────────────
def scan_once():
    """掃描一次"""
    watchlist = load_watchlist()
    if not watchlist:
        log("⚠️ 監控清單為空，從 products.json 載入...")
        watchlist = bootstrap_from_products()
        if not watchlist:
            log("❌ 沒有 SKU 可掃描，請用 --add-sku 加入或先跑 hermes_monitor_cloud.py")
            return

    state = load_state()
    notified = set(state.get("notified", []))
    known_before = set(load_known_skus())  # 上次掃描看到的 SKU
    starred = load_starred()
    # starred SKU 上次警報時間（避免狀態 flapping 時爆 LINE，每 4h 一次）
    star_alert_log = state.get("star_alerts", {})
    now_ts = time.time()
    STAR_REALERT_INTERVAL = 4 * 3600  # 4 小時

    log(f"🔍 開始掃描 {len(watchlist)} 個 SKU...")
    # workers=30：936 SKU 在 ~10 秒內完成（CDN 是 image server，不會被 rate-limit）
    results = probe_batch(watchlist, workers=30)

    found_existing = 0
    new_alerts = 0
    star_alerts = 0
    known_now = set()

    for r in results:
        if r.get("exists"):
            sku = r["sku"]
            found_existing += 1
            known_now.add(sku)

            # 星標 SKU：「不在上次 known 集合 → 現在存在」才警報（首次出現）
            # 加 4h 復警報冷卻避免 flapping
            if sku in starred:
                if sku not in known_before:
                    last = star_alert_log.get(sku, 0)
                    if now_ts - last >= STAR_REALERT_INTERVAL:
                        log(f"⭐ 星標款首次出現！SKU: {sku}")
                        if send_line_alert(sku, starred=True):
                            star_alert_log[sku] = now_ts
                            star_alerts += 1
                        trigger_auto_buy(sku)
                        notified.add(sku)
                        continue
                # 已存在 → 不警報，但也不要再走後面的「新發現」邏輯
                continue

            if sku not in notified:
                log(f"🚨 新發現！SKU: {sku}")
                if send_line_alert(sku):
                    notified.add(sku)
                    new_alerts += 1
                trigger_auto_buy(sku)

    state["star_alerts"] = star_alert_log
    log(f"✅ 掃描完成：CDN 上有 {found_existing} 個 SKU，新警報 {new_alerts} 則" +
        (f"，⭐ 星標警報 {star_alerts} 則" if star_alerts else ""))

    state["notified"] = list(notified)
    save_state(state)
    save_known_skus(list(known_now))


def loop_scan(interval: int = 30):
    """持續掃描"""
    log(f"🔄 進入持續掃描模式（每 {interval} 秒一次）")
    while True:
        try:
            scan_once()
        except Exception as e:
            log(f"❌ 掃描例外: {e}")
        time.sleep(interval)


# ─── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="愛馬仕 CDN 早期警報")
    parser.add_argument("--loop", action="store_true", help="持續掃描模式")
    parser.add_argument("--interval", type=int, default=30, help="掃描間隔秒數")
    parser.add_argument("--add-sku", help="加入 SKU 到監控清單")
    parser.add_argument("--list", action="store_true", help="列出監控中的 SKU")
    parser.add_argument("--bootstrap", action="store_true", help="從 products.json 載入")
    parser.add_argument("--test", action="store_true", help="測試 LINE 通知")
    parser.add_argument("--star", help="加星標 SKU（永遠會通知，每 4h 一次）")
    parser.add_argument("--unstar", help="移除星標")
    parser.add_argument("--list-stars", action="store_true", help="列出所有星標 SKU")
    args = parser.parse_args()

    if args.star:
        sku = args.star.upper().strip()
        if add_star(sku):
            print(f"⭐ 已加入星標: {sku}")
            print(f"   目前星標 SKU 數: {len(load_starred())}")
        else:
            print(f"⚠️ 已是星標: {sku}")
        return

    if args.unstar:
        sku = args.unstar.upper().strip()
        if remove_star(sku):
            print(f"✅ 已移除星標: {sku}")
        else:
            print(f"⚠️ 不在星標清單: {sku}")
        return

    if args.list_stars:
        starred = sorted(load_starred())
        print(f"⭐ 星標 SKU ({len(starred)} 個):")
        for s in starred:
            print(f"  {s}")
        return

    if args.add_sku:
        sku = args.add_sku.upper().strip()
        if not SKU_PATTERN.match(sku):
            print(f"❌ SKU 格式錯誤，應為 6 位數+2 英文+2 數字 (例如 084948CP89)")
            return
        watchlist = load_watchlist()
        if sku not in watchlist:
            watchlist.append(sku)
            save_watchlist(watchlist)
            print(f"✅ 已加入: {sku}")
        else:
            print(f"⚠️ 已在清單中: {sku}")
        return

    if args.list:
        watchlist = load_watchlist()
        print(f"監控清單 ({len(watchlist)} 個):")
        for s in watchlist:
            print(f"  {s}")
        return

    if args.bootstrap:
        bootstrap_from_products()
        return

    if args.test:
        send_line_alert("TEST00CK00")
        return

    if args.loop:
        loop_scan(args.interval)
    else:
        scan_once()


if __name__ == "__main__":
    main()
