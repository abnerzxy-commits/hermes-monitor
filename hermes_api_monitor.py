#!/usr/bin/env python3
"""
愛馬仕快速 API 監控（取代 Playwright 版）
==========================================

原理：
- Hermes 用 Angular SSR，商品資料嵌在 <script id="hermes-state"> 裡
- 純 HTTP GET 就能拿到完整商品清單，不需要開瀏覽器
- 速度：Playwright 30-60 秒 → API 模式 1-3 秒
- 不會被 DataDome 擋（SSR 頁面通常放行正常 UA）

防封鎖策略：
- 隨機 User-Agent 輪換
- 請求間隔 3-8 秒隨機延遲
- 每次只爬必要的分類頁
- 失敗時指數退避

執行：
    python3 hermes_api_monitor.py           # 單次掃描
    python3 hermes_api_monitor.py --test    # 測試模式
"""
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# ─── Config ────────────────────────────────────────────
HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

PRODUCTS_FILE = DATA_DIR / "products.json"
HISTORY_FILE = DATA_DIR / "restock_history.json"
LOG_FILE = DATA_DIR / "monitor.log"
CDN_STATE_FILE = DATA_DIR / "cdn_state.json"
KNOWN_URLS_FILE = DATA_DIR / "known_product_urls.json"
WISHLIST_FILE = DATA_DIR / "wishlist.json"

SKU_PATTERN = re.compile(r"H?(\d{6}[A-Z]{2}[A-Z0-9]{2})", re.IGNORECASE)

# 分類頁 URL（只用確認能 200 的頁面，避免 403 浪費重試時間）
CATEGORY_URLS = [
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
    "https://www.hermes.com/tw/zh/category/leather-goods/small-leather-goods/",
]

# 隨機 UA 池（避免特徵被辨識）
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

DEFAULT_WISHLIST = [
    "Birkin", "Kelly", "Constance", "Picotin", "Lindy",
    "Evelyne", "Bolide", "Garden Party", "Mini Kelly",
]


# ─── Logging ────────────────────────────────────────────
def log(msg: str):
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ─── HTTP Session ────────────────────────────────────────
def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Accept-Encoding": "identity",
    })
    return s


# ─── Parse hermes-state JSON ────────────────────────────
STATE_RE = re.compile(
    r'<script\s+id="hermes-state"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


def extract_products_from_html(html: str) -> list[dict]:
    """從 Angular SSR HTML 中提取商品資料"""
    match = STATE_RE.search(html)
    if not match:
        # Fallback: 有時候 id 沒引號或格式不同
        match = re.search(
            r'<script[^>]+id=["\']?hermes-state["\']?[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
    if not match:
        log("  ⚠️ 找不到 hermes-state script tag")
        return []

    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        log(f"  ⚠️ hermes-state JSON 解析失敗: {e}")
        return []

    products = []
    for key, entry in state.items():
        if not isinstance(entry, dict):
            continue
        body = entry.get("b")
        if not isinstance(body, dict):
            continue
        # 找 products.items（不依賴 URL 比對）
        prod_data = body.get("products")
        if not isinstance(prod_data, dict):
            continue
        items = prod_data.get("items")
        if not items:
            continue

        for item in items:
            sku = item.get("sku", "")
            title = item.get("title", "")
            price = item.get("price", 0)
            url_path = item.get("url", "")
            stock = item.get("stock", {})
            assets = item.get("assets", [])

            # 取第一張圖（front 優先）
            image = ""
            for asset in assets:
                img_url = asset.get("url", "")
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if asset.get("tag") == "front":
                    image = img_url
                    break
                if not image:
                    image = img_url

            full_url = f"https://www.hermes.com/tw/zh{url_path}" if url_path else ""

            products.append({
                "sku": sku,
                "name": title,
                "price": f"NT$ {price:,}" if price else "",
                "price_raw": price,
                "url": full_url,
                "image": image,
                "in_stock": stock.get("ecom", False),
                "color": item.get("avgColor", ""),
            })

    return products


# ─── Scrape via API ──────────────────────────────────────
def scrape_hermes_api() -> list[dict]:
    """純 HTTP GET 爬取所有分類頁，提取商品資料"""
    session = _build_session()
    all_products = []
    seen_skus = set()

    for i, url in enumerate(CATEGORY_URLS):
        if i > 0:
            delay = random.uniform(3, 8)
            log(f"  等待 {delay:.1f} 秒...")
            time.sleep(delay)

        log(f"正在爬取: {url}")
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=30)
                log(f"  HTTP {resp.status_code} ({len(resp.text)} bytes)")

                if resp.status_code == 403:
                    log(f"  ⚠️ 被擋 (403)，等待重試...")
                    time.sleep(10 * (attempt + 1))
                    session = _build_session()  # 換 UA
                    continue

                if resp.status_code != 200:
                    log(f"  ⚠️ 非 200 狀態，跳過")
                    break

                products = extract_products_from_html(resp.text)
                new_count = 0
                for p in products:
                    if p["sku"] not in seen_skus:
                        seen_skus.add(p["sku"])
                        all_products.append(p)
                        new_count += 1

                log(f"  找到 {len(products)} 個產品（新增 {new_count}）")
                break  # 成功，跳出重試

            except requests.exceptions.Timeout:
                log(f"  ⏰ 超時，重試 ({attempt + 1}/3)")
                time.sleep(5)
            except Exception as e:
                log(f"  ❌ 錯誤: {e}")
                time.sleep(5)

    log(f"總共找到 {len(all_products)} 個產品（API 模式）")
    return all_products


# ─── Product processing ─────────────────────────────────
def make_product(data: dict) -> dict:
    url = data.get("url", "")
    return {
        "id": hashlib.md5(url.encode()).hexdigest()[:12],
        "name": data.get("name", ""),
        "url": url,
        "image": data.get("image", ""),
        "price": data.get("price", ""),
        "first_seen": datetime.now().isoformat(),
    }


def load_previous_products() -> dict:
    if PRODUCTS_FILE.exists():
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_products(products: list[dict]):
    DATA_DIR.mkdir(exist_ok=True)
    product_map = {p["id"]: p for p in products}
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(product_map, f, ensure_ascii=False, indent=2)


def find_new_products(current: list[dict], previous: dict) -> list[dict]:
    return [p for p in current if p["id"] not in previous]


# ─── Wishlist ────────────────────────────────────────────
def load_wishlist() -> list[str]:
    if WISHLIST_FILE.exists():
        with open(WISHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    DATA_DIR.mkdir(exist_ok=True)
    with open(WISHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_WISHLIST, f, ensure_ascii=False, indent=2)
    return DEFAULT_WISHLIST


def is_wishlist_match(product: dict, wishlist: list[str]) -> bool:
    name = product.get("name", "").lower()
    url = product.get("url", "").lower()
    text = f"{name} {url}"
    return any(w.lower() in text for w in wishlist)


# ─── CDN state ───────────────────────────────────────────
def load_cdn_notified_skus() -> set[str]:
    if CDN_STATE_FILE.exists():
        try:
            with open(CDN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            return set(state.get("notified", []))
        except Exception:
            pass
    return set()


def extract_sku(url: str) -> str | None:
    match = SKU_PATTERN.search(url)
    return match.group(1).upper() if match else None


# ─── Restock history ─────────────────────────────────────
def record_restock_history(new_products: list[dict]):
    history = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    now = datetime.now()
    for p in new_products:
        history.append({
            "name": p.get("name", ""),
            "url": p.get("url", ""),
            "price": p.get("price", ""),
            "timestamp": now.isoformat(),
            "weekday": now.strftime("%A"),
            "hour": now.hour,
        })
    history = history[-500:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def parse_price(price_str: str) -> float:
    nums = re.sub(r"[^\d.]", "", price_str)
    return float(nums) if nums else 0


def get_price_comparison(product: dict) -> str:
    if not HISTORY_FILE.exists():
        return ""
    current_price = parse_price(product.get("price", ""))
    if current_price <= 0:
        return ""
    name = product.get("name", "").lower()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    highest = 0
    for entry in history:
        if entry.get("name", "").lower() == name or name in entry.get("name", "").lower():
            hist_price = parse_price(entry.get("price", ""))
            if hist_price > highest:
                highest = hist_price
    if highest > 0 and current_price < highest:
        diff = highest - current_price
        pct = round(diff / highest * 100)
        return f"📉 降價{pct}% (原NT${int(highest):,})"
    return ""


def get_restock_stats() -> str:
    if not HISTORY_FILE.exists():
        return ""
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    if len(history) < 3:
        return ""
    weekday_counts = {}
    hour_counts = {}
    for entry in history:
        wd = entry.get("weekday", "")
        hr = entry.get("hour", 0)
        weekday_counts[wd] = weekday_counts.get(wd, 0) + 1
        hour_counts[hr] = hour_counts.get(hr, 0) + 1
    top_day = max(weekday_counts, key=weekday_counts.get) if weekday_counts else ""
    top_hour = max(hour_counts, key=hour_counts.get) if hour_counts else 0
    return f"📊 歷史統計：最常補貨 {top_day} {top_hour}:00 | 共記錄 {len(history)} 次"


# ─── LINE notification ───────────────────────────────────
def build_flex_message(product: dict, is_wishlist: bool, is_cdn_confirmed: bool = False) -> dict:
    name = product.get("name", "未知品名")
    url = product.get("url", "")
    image = product.get("image", "")
    price = product.get("price", "")

    if is_wishlist:
        tag = "🔥 熱門款式！"
        tag_color = "#FF0000"
    elif is_cdn_confirmed:
        tag = "✅ 正式上架！（CDN 已預告）"
        tag_color = "#00A600"
    else:
        tag = "🆕 新品上架"
        tag_color = "#FF6B00"

    bubble = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": tag, "weight": "bold", "color": tag_color, "size": "sm"},
                {"type": "text", "text": name, "weight": "bold", "size": "lg", "wrap": True, "margin": "md"},
            ],
            "paddingAll": "15px",
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {"type": "uri", "label": "立即購買 🛒", "uri": url},
                    "style": "primary",
                    "color": "#FF6B00",
                },
            ],
            "paddingAll": "10px",
        },
    }

    if image and image.startswith("http"):
        bubble["hero"] = {
            "type": "image", "url": image,
            "size": "full", "aspectRatio": "4:3", "aspectMode": "cover",
        }

    if price:
        bubble["body"]["contents"].append(
            {"type": "text", "text": f"💰 {price}", "size": "md", "color": "#333333", "margin": "sm"}
        )

    price_note = get_price_comparison(product)
    if price_note:
        bubble["body"]["contents"].append({
            "type": "text", "text": price_note,
            "size": "sm", "color": "#E5004F", "weight": "bold", "margin": "sm", "wrap": True,
        })

    return bubble


def send_line_notification(new_products: list[dict]):
    if not LINE_TOKEN or LINE_TOKEN == "your_token_here":
        log("⚠️ LINE Token 未設定")
        return False
    if not LINE_USER_ID or LINE_USER_ID == "your_user_id_here":
        log("⚠️ LINE User ID 未設定")
        return False

    wishlist = load_wishlist()
    cdn_notified = load_cdn_notified_skus()

    def is_cdn_confirmed(product: dict) -> bool:
        sku = extract_sku(product.get("url", ""))
        return sku is not None and sku in cdn_notified

    wishlist_products = [p for p in new_products if is_wishlist_match(p, wishlist)]
    cdn_confirmed_products = [p for p in new_products
                              if not is_wishlist_match(p, wishlist) and is_cdn_confirmed(p)]
    normal_products = [p for p in new_products
                       if not is_wishlist_match(p, wishlist) and not is_cdn_confirmed(p)]

    messages = []

    if wishlist_products:
        bubbles = [build_flex_message(p, is_wishlist=True, is_cdn_confirmed=is_cdn_confirmed(p))
                   for p in wishlist_products[:5]]
        messages.append({
            "type": "flex",
            "altText": f"🔥 熱門款式！{len(wishlist_products)} 件",
            "contents": {"type": "carousel", "contents": bubbles},
        })

    if cdn_confirmed_products:
        bubbles = [build_flex_message(p, is_wishlist=False, is_cdn_confirmed=True)
                   for p in cdn_confirmed_products[:5]]
        messages.append({
            "type": "flex",
            "altText": f"✅ 正式上架！{len(cdn_confirmed_products)} 件（CDN 已預告）",
            "contents": {"type": "carousel", "contents": bubbles},
        })

    if normal_products:
        bubbles = [build_flex_message(p, is_wishlist=False) for p in normal_products[:5]]
        messages.append({
            "type": "flex",
            "altText": f"🧡 愛馬仕新品 {len(normal_products)} 件",
            "contents": {"type": "carousel", "contents": bubbles},
        })

    footer = "蕭key來買喔～"
    stats = get_restock_stats()
    if stats:
        footer = f"{stats}\n\n蕭key來買喔～"
    messages.append({"type": "text", "text": footer})

    if len(messages) <= 1:
        return False

    messages = messages[:5]

    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_TOKEN}",
            },
            json={"messages": messages},
            timeout=10,
        )
        if resp.status_code == 200:
            log(f"✅ LINE 通知成功（熱門 {len(wishlist_products)} + CDN確認 {len(cdn_confirmed_products)} + 一般 {len(normal_products)} 件）")
            return True
        else:
            log(f"❌ LINE 通知失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log(f"❌ LINE 通知例外: {e}")
        return False


# ─── Main ────────────────────────────────────────────────
def main():
    log("=" * 50)
    log("開始愛馬仕新品監控（API 快速模式）")

    current_raw = scrape_hermes_api()

    if not current_raw:
        log("⚠️ 未爬到任何產品")
        return

    # 轉換為統一格式
    current_products = [make_product(p) for p in current_raw]

    # 更新已知 URL
    known_urls = []
    if KNOWN_URLS_FILE.exists():
        try:
            with open(KNOWN_URLS_FILE, "r", encoding="utf-8") as f:
                known_urls = json.load(f)
        except Exception:
            pass
    current_urls = list(set(known_urls + [p["url"] for p in current_products if p.get("url")]))
    with open(KNOWN_URLS_FILE, "w", encoding="utf-8") as f:
        json.dump(current_urls, f, ensure_ascii=False, indent=2)

    previous = load_previous_products()

    if previous:
        new_products = find_new_products(current_products, previous)
        if new_products:
            cdn_notified = load_cdn_notified_skus()
            cdn_count = sum(1 for p in new_products
                           if extract_sku(p.get("url", "")) in cdn_notified)
            log(f"🆕 發現 {len(new_products)} 件新品！" +
                (f"（其中 {cdn_count} 件 CDN 已預告）" if cdn_count else ""))
            for p in new_products:
                sku = extract_sku(p.get("url", ""))
                cdn_tag = " [CDN✅]" if sku and sku in cdn_notified else ""
                log(f"  - {p.get('name', '未知')}{cdn_tag} | {p.get('url', '')}")
            record_restock_history(new_products)
            send_line_notification(new_products)
        else:
            log("沒有新品上架")
    else:
        log(f"首次執行，記錄 {len(current_products)} 個現有產品（不發通知）")

    save_products(current_products)
    log("監控完成（API 模式）")
    log("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        log("測試模式：嘗試 API 爬取...")
        products = scrape_hermes_api()
        for p in products:
            log(f"  {p['name']} | {p['price']} | SKU: {p['sku']} | 庫存: {p['in_stock']}")
    else:
        main()
