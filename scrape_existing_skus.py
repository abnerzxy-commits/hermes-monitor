#!/usr/bin/env python3
"""
從 hermes.com 各包包品類頁面抓取所有現有 SKU
用 Chrome debug session（已登入），避開 DataDome
輸出到 data/scraped_skus.json + 自動 merge 進 watchlist

執行：
    python3 scrape_existing_skus.py             # 全量爬蟲
    python3 scrape_existing_skus.py --dry-run   # 不寫 watchlist
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

WATCHLIST_FILE = DATA_DIR / "sku_watchlist.json"
SCRAPED_FILE = DATA_DIR / "scraped_skus.json"
STATE_FILE = DATA_DIR / "cdn_state.json"  # 已通知過的 SKU
LOG_FILE = DATA_DIR / "scraper.log"

# 多區域監控（驗證 2026-04-09：US/JP/TW 各有獨佔 SKU，FR 也獨立庫存）
# Per-region path mapping — 大部分用 leather-goods/* 但 FR 用法文 maroquinerie/*
REGIONS = [
    ("tw", "tw/zh"),
    ("us", "us/en"),
    ("fr", "fr/fr"),
    ("jp", "jp/ja"),
]

# 預設英文路徑（tw/us/jp 都用這個）
# 2026-04-11: 加入更多子分類，避免漏掉 Lindy/Evelyne 等熱門款
DEFAULT_CATEGORY_PATHS = [
    "category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
    "category/leather-goods/bags-and-clutches/mens-bags-and-clutches/",
    "category/leather-goods/bags-and-clutches/",
    # 個別系列頁（分類頁可能不會列出所有商品）
    "category/leather-goods/bags-and-clutches/lindy/",
    "category/leather-goods/bags-and-clutches/evelyne/",
    "category/leather-goods/bags-and-clutches/birkin/",
    "category/leather-goods/bags-and-clutches/kelly/",
    "category/leather-goods/bags-and-clutches/constance/",
    "category/leather-goods/bags-and-clutches/picotin/",
    "category/leather-goods/bags-and-clutches/bolide/",
    "category/leather-goods/bags-and-clutches/garden-party/",
    "category/leather-goods/bags-and-clutches/verrou/",
    "category/leather-goods/bags-and-clutches/halzan/",
    "category/leather-goods/bags-and-clutches/herbag/",
    "category/leather-goods/bags-and-clutches/in-the-loop/",
    "category/leather-goods/small-leather-goods/",
    "category/leather-goods/small-leather-goods/to-go-wallets/",
    "category/leather-goods/small-leather-goods/wallets/",
    "category/leather-goods/small-leather-goods/card-holders/",
    "category/leather-goods/small-leather-goods/pouches-and-cases/",
    "category/leather-goods/accessories/charms/",
    "category/leather-goods/accessories/straps/",
    "category/leather-goods/luggage/travel-accessories/",
]

# FR 用法文路徑
FR_CATEGORY_PATHS = [
    "category/maroquinerie/sacs-et-pochettes/sacs-et-pochettes-femme/",
    "category/maroquinerie/sacs-et-pochettes/sacs-et-pochettes-homme/",
    "category/maroquinerie/sacs-et-pochettes/",
    "category/maroquinerie/sacs-et-pochettes/lindy/",
    "category/maroquinerie/sacs-et-pochettes/evelyne/",
    "category/maroquinerie/sacs-et-pochettes/birkin/",
    "category/maroquinerie/sacs-et-pochettes/kelly/",
    "category/maroquinerie/sacs-et-pochettes/constance/",
    "category/maroquinerie/sacs-et-pochettes/picotin/",
    "category/maroquinerie/petite-maroquinerie/",
    "category/maroquinerie/petite-maroquinerie/portefeuilles-to-go/",
    "category/maroquinerie/petite-maroquinerie/portefeuilles/",
    "category/maroquinerie/petite-maroquinerie/porte-cartes/",
    "category/maroquinerie/petite-maroquinerie/trousses-et-etuis/",
]

REGION_PATHS = {
    "tw": DEFAULT_CATEGORY_PATHS,
    "us": DEFAULT_CATEGORY_PATHS,
    "jp": DEFAULT_CATEGORY_PATHS,
    "fr": FR_CATEGORY_PATHS,
}

# 為了 backward compat，CATEGORY_PATHS 還在
CATEGORY_PATHS = DEFAULT_CATEGORY_PATHS


def build_category_urls() -> list:
    """生成所有 region × category 的 URL 組合"""
    out = []
    for region, locale in REGIONS:
        paths = REGION_PATHS.get(region, DEFAULT_CATEGORY_PATHS)
        for path in paths:
            out.append((region, f"https://www.hermes.com/{locale}/{path}"))
    return out


CATEGORY_URLS = build_category_urls()

# SKU 格式：6 位數 + 2 字母 + 2 字符（可數字或字母，例如 CP89 / CKAB / CKP0 / CK4B）
SKU_FROM_URL = re.compile(r"-H?(\d{6}[A-Z]{2}[A-Z0-9]{2})", re.IGNORECASE)
SKU_PREFIX_FROM_URL = re.compile(r"-H?(\d{6})(?:[/-]|$)")


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def scrape_category(page, url: str, max_scrolls: int = 8) -> set[str]:
    """爬一個品類頁，回傳所有找到的 SKU"""
    found = set()
    log(f"  → goto {url[:90]}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log(f"    ❌ goto 失敗: {e}")
    # 多等讓 SPA hydrate（不同 region 速度差異大）
    time.sleep(4)

    # 偵測 captcha（容錯：頁面正在 navigate 時 content() 會 throw）
    try:
        html = page.content()
    except Exception:
        time.sleep(2)
        try:
            html = page.content()
        except Exception as e:
            log(f"    ❌ content 失敗: {e}")
            return found
    if "captcha-delivery" in html:
        log(f"    ⚠️ DataDome captcha — 跳過")
        return found

    # 滑動載入更多商品
    for i in range(max_scrolls):
        try:
            page.evaluate("() => window.scrollBy(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(1.2)

    # 抓所有 product link
    try:
        links = page.query_selector_all('a[href*="/product/"]')
    except Exception:
        links = []
    log(f"    收集到 {len(links)} 個 product link")

    for link in links:
        try:
            href = link.get_attribute("href")
            if not href:
                continue
            # 試試完整 SKU
            m = SKU_FROM_URL.search(href)
            if m:
                found.add(m.group(1).upper())
                continue
            # 退而求其次：只有 prefix
            m2 = SKU_PREFIX_FROM_URL.search(href)
            if m2:
                # prefix only — 還是記錄起來，後面 ML 預測會用
                found.add("PREFIX:" + m2.group(1))
        except Exception:
            pass
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="不寫 watchlist")
    parser.add_argument("--max-scrolls", type=int, default=8)
    args = parser.parse_args()

    log(f"🕷️ 開始爬蟲，目標 {len(CATEGORY_URLS)} 個品類頁（{len(REGIONS)} 個地區）")

    all_skus = set()
    all_prefixes = set()
    per_region: dict[str, set] = {r: set() for r, _ in REGIONS}

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            log(f"❌ 無法連線 Chrome debug (port 9222): {e}")
            log("   請先執行 ./start_chrome_debug.sh")
            sys.exit(1)

        ctx = browser.contexts[0]
        # 用一個專屬 page 爬，不影響主分頁
        page = ctx.new_page()

        try:
            for region, url in CATEGORY_URLS:
                results = scrape_category(page, url, max_scrolls=args.max_scrolls)
                for sku in results:
                    if sku.startswith("PREFIX:"):
                        all_prefixes.add(sku[7:])
                    else:
                        all_skus.add(sku)
                        per_region[region].add(sku)
                log(f"    [{region}] 累計 SKU={len(all_skus)} (region={len(per_region[region])})")
        finally:
            page.close()

    log(f"✅ 爬完，總計 {len(all_skus)} 個完整 SKU + {len(all_prefixes)} 個 prefix")
    for region, skus in per_region.items():
        log(f"   {region}: {len(skus)} SKU")

    # 寫 scraped_skus.json
    payload = {
        "scraped_at": datetime.now().isoformat(),
        "skus": sorted(all_skus),
        "prefixes": sorted(all_prefixes),
        "per_region": {r: sorted(s) for r, s in per_region.items()},
        "category_paths": CATEGORY_PATHS,
        "regions": [r for r, _ in REGIONS],
    }
    with open(SCRAPED_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"📁 寫到 {SCRAPED_FILE}")

    if args.dry_run:
        log("🚫 dry-run，不更新 watchlist")
        return

    # Merge into watchlist
    existing = []
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE) as f:
                existing = json.load(f)
        except Exception:
            pass
    new_skus = all_skus - set(existing)  # 真正新增的
    merged = sorted(set(existing) | all_skus)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    log(f"✅ watchlist: {len(existing)} → {len(merged)} (+{len(new_skus)})")

    # 關鍵：自動把『新增到 watchlist 的 SKU』標記為已通知
    # 防止下次掃描把『現有商品』當『新品』爆 LINE
    if new_skus:
        state = {}
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
            except Exception:
                pass
        notified = set(state.get("notified", []))
        before = len(notified)
        notified |= new_skus
        state["notified"] = sorted(notified)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        log(f"🔇 自動靜音新增 SKU: {before} → {len(notified)} (避免誤判為新品)")


if __name__ == "__main__":
    main()
