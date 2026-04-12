#!/usr/bin/env python3
"""
愛馬仕新品包包監控（雲端版 v2）
功能：圖片預覽、熱門款式、產品頁掃描、一鍵購買、補貨歷史
"""

import json
import os
import re
import sys
import hashlib
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# DataDome 解題
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from datadome_solver import solve_datadome, setup_solver, with_datadome_bypass
    setup_solver()
    HAS_SOLVER = True
except ImportError:
    HAS_SOLVER = False

# 分類頁 URL（2026-04-11: 加入各系列子頁，避免漏掉 Lindy/Evelyne 等）
HERMES_CATEGORY_URLS = [
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/lindy/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/evelyne/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/birkin/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/kelly/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/constance/",
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/picotin/",
]

# 個別產品頁 URL（這些產品會比分類頁更早出現，提前 10-20 分鐘通知）
# 系統會自動從分類頁學到的產品 URL 加進來
HERMES_PRODUCT_URLS_FILE = Path(__file__).parent / "data" / "known_product_urls.json"

# 熱門款式（包含這些關鍵字的產品會特別標記）
WISHLIST_FILE = Path(__file__).parent / "data" / "wishlist.json"
DEFAULT_WISHLIST = [
    "Birkin", "Kelly", "Constance", "Picotin", "Lindy",
    "Evelyne", "Bolide", "Garden Party", "Mini Kelly",
]

DATA_DIR = Path(__file__).parent / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"
HISTORY_FILE = DATA_DIR / "restock_history.json"
LOG_FILE = DATA_DIR / "monitor.log"
CDN_STATE_FILE = DATA_DIR / "cdn_state.json"

SKU_PATTERN = re.compile(r"H?(\d{6}[A-Z]{2}\d{2})", re.IGNORECASE)


def log(msg: str):
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_wishlist() -> list[str]:
    """載入熱門款式"""
    if WISHLIST_FILE.exists():
        with open(WISHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 首次執行建立預設熱門款式
    DATA_DIR.mkdir(exist_ok=True)
    with open(WISHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_WISHLIST, f, ensure_ascii=False, indent=2)
    return DEFAULT_WISHLIST


def is_wishlist_match(product: dict, wishlist: list[str]) -> bool:
    """檢查產品是否符合熱門款式"""
    name = product.get("name", "").lower()
    url = product.get("url", "").lower()
    text = f"{name} {url}"
    return any(w.lower() in text for w in wishlist)


def load_cdn_notified_skus() -> set[str]:
    """載入 CDN 早期預警已通知過的 SKU"""
    if CDN_STATE_FILE.exists():
        try:
            with open(CDN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            return set(state.get("notified", []))
        except Exception:
            pass
    return set()


def extract_sku(url: str) -> str | None:
    """從產品 URL 中提取 SKU"""
    match = SKU_PATTERN.search(url)
    return match.group(1).upper() if match else None


def load_known_product_urls() -> list[str]:
    """載入已知的產品頁 URL"""
    if HERMES_PRODUCT_URLS_FILE.exists():
        with open(HERMES_PRODUCT_URLS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_known_product_urls(urls: list[str]):
    """儲存已知的產品頁 URL"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(HERMES_PRODUCT_URLS_FILE, "w", encoding="utf-8") as f:
        json.dump(urls, f, ensure_ascii=False, indent=2)


def solve_datadome_captcha(page, page_url: str) -> bool:
    """用共用模組解 DataDome CAPTCHA"""
    if not HAS_SOLVER:
        log("  DataDome solver 未載入，跳過")
        return False
    return solve_datadome(page, page_url)


def scrape_hermes() -> list[dict]:
    """用 Playwright + stealth 爬愛馬仕（分類頁 + 個別產品頁），自動重試"""
    # 最多重試 3 次
    for attempt in range(3):
        result = _scrape_hermes_once(attempt)
        if result:
            return result
        log(f"  第 {attempt + 1} 次嘗試失敗，重試中...")
        time.sleep(3)
    log("⚠️ 3 次嘗試都失敗")
    return []


def _scrape_hermes_once(attempt: int) -> list[dict]:
    """單次爬取嘗試"""
    all_products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
            viewport={"width": 1920, "height": 1080},
        )

        # 套用 stealth（如果有安裝）
        stealth = None
        if HAS_STEALTH:
            stealth = Stealth()

        # === 1. 爬分類頁 ===
        for url in HERMES_CATEGORY_URLS:
            log(f"正在爬取分類頁: {url}")
            try:
                page = context.new_page()
                if stealth:
                    stealth.apply_stealth_sync(page)
                resp = page.goto(url, wait_until="networkidle", timeout=60000)
                status = resp.status if resp else 0
                log(f"  HTTP 狀態: {status}")

                if status == 403:
                    log("  ⚠️ 被 DataDome 擋住 (403)，嘗試解 CAPTCHA...")
                    solved = solve_datadome_captcha(page, url)
                    if solved:
                        log("  ✅ CAPTCHA 解開！重新載入頁面")
                        resp = page.goto(url, wait_until="networkidle", timeout=60000)
                        status = resp.status if resp else 0
                        log(f"  重新載入 HTTP 狀態: {status}")
                    if status == 403:
                        log("  ❌ CAPTCHA 解題失敗")
                        page.close()
                        browser.close()
                        return []  # 觸發重試

                page.wait_for_timeout(5000)
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)

                # 從分類頁一次抓齊：名稱、價格、圖片、連結
                products_data = page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/product/"]');
                        const imgs = document.querySelectorAll('img[src*="hermesproduct"]');
                        const products = [];
                        const seen = {};

                        // 收集所有產品圖片
                        const imgList = Array.from(imgs).map(i => i.src || '');

                        // 從 script 裡找價格
                        const prices = [];
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const t = s.textContent || '';
                            const m = t.match(/"name":"([\d.]+)","selected"/g);
                            if (m) {
                                for (const match of m) {
                                    const p = match.match(/"name":"([\d.]+)"/);
                                    if (p) prices.push(parseFloat(p[1]));
                                }
                            }
                        }

                        let idx = 0;
                        for (const a of links) {
                            const href = a.href;
                            if (seen[href]) continue;
                            seen[href] = true;
                            const name = a.textContent.trim().replace(/\\s+/g, ' ').substring(0, 200);
                            const image = imgList[idx] || '';
                            const price = prices[idx] ? 'NT$ ' + prices[idx].toLocaleString() : '';
                            products.push({name, url: href, image, price});
                            idx++;
                        }
                        return products;
                    }
                """)

                log(f"  分類頁找到 {len(products_data)} 個產品")
                for pd in products_data:
                    product = make_product(pd)
                    if not any(ep["id"] == product["id"] for ep in all_products):
                        all_products.append(product)
                        log(f"  ✅ {pd.get('name', '')} | {pd.get('price', '')} | 圖:{bool(pd.get('image'))}")

                page.close()

            except Exception as e:
                log(f"  分類頁爬取失敗: {e}")

        # === 3. 更新已知產品 URL 清單 ===
        known_urls = load_known_product_urls()
        current_urls = list(set(
            known_urls + [p["url"] for p in all_products if p.get("url")]
        ))
        save_known_product_urls(current_urls)

        browser.close()

    log(f"總共找到 {len(all_products)} 個產品")
    return all_products


def make_product(data: dict) -> dict:
    """建立統一的產品 dict"""
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


# === 補貨歷史 ===

def record_restock_history(new_products: list[dict]):
    """記錄補貨歷史"""
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

    # 只保留最近 500 筆
    history = history[-500:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def parse_price(price_str: str) -> float:
    """解析價格字串為數字，例如 'NT$ 252,300' → 252300.0"""
    import re
    nums = re.sub(r"[^\d.]", "", price_str)
    return float(nums) if nums else 0


def get_price_comparison(product: dict) -> str:
    """比對產品的歷史價格，如果現在比較便宜就回傳提示"""
    if not HISTORY_FILE.exists():
        return ""

    current_price = parse_price(product.get("price", ""))
    if current_price <= 0:
        return ""

    name = product.get("name", "").lower()

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    # 找同名產品的歷史最高價
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
    """取得補貨統計摘要"""
    if not HISTORY_FILE.exists():
        return ""
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    if len(history) < 3:
        return ""

    # 統計星期幾最常補貨
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


# === LINE 通知（Flex Message 帶圖片） ===

def build_flex_message(product: dict, is_wishlist: bool, is_cdn_confirmed: bool = False) -> dict:
    """建立單個產品的 Flex Message bubble"""
    name = product.get("name", "未知品名")
    url = product.get("url", "")
    image = product.get("image", "")
    price = product.get("price", "")

    # 標記優先順序：熱門款式 > CDN 確認上架 > 一般新品
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
                {
                    "type": "text",
                    "text": tag,
                    "weight": "bold",
                    "color": tag_color,
                    "size": "sm",
                },
                {
                    "type": "text",
                    "text": name,
                    "weight": "bold",
                    "size": "lg",
                    "wrap": True,
                    "margin": "md",
                },
            ],
            "paddingAll": "15px",
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "立即購買 🛒",
                        "uri": url,
                    },
                    "style": "primary",
                    "color": "#FF6B00",
                },
            ],
            "paddingAll": "10px",
        },
    }

    # 加圖片（如果有）
    if image and image.startswith("http"):
        bubble["hero"] = {
            "type": "image",
            "url": image,
            "size": "full",
            "aspectRatio": "4:3",
            "aspectMode": "cover",
        }

    # 加價格（如果有）
    if price:
        bubble["body"]["contents"].append({
            "type": "text",
            "text": f"💰 {price}",
            "size": "md",
            "color": "#333333",
            "margin": "sm",
        })

    # 比對歷史價格
    price_note = get_price_comparison(product)
    if price_note:
        bubble["body"]["contents"].append({
            "type": "text",
            "text": price_note,
            "size": "sm",
            "color": "#E5004F",
            "weight": "bold",
            "margin": "sm",
            "wrap": True,
        })

    return bubble


def send_line_notification(new_products: list[dict]):
    """發送 LINE Flex Message（帶圖片 + 一鍵購買）"""
    if not LINE_TOKEN or LINE_TOKEN == "your_token_here":
        log("⚠️ LINE Token 未設定")
        return False
    if not LINE_USER_ID or LINE_USER_ID == "your_user_id_here":
        log("⚠️ LINE User ID 未設定")
        return False

    wishlist = load_wishlist()
    cdn_notified = load_cdn_notified_skus()

    # 檢查每個產品是否曾被 CDN 預警通知過
    def is_cdn_confirmed(product: dict) -> bool:
        sku = extract_sku(product.get("url", ""))
        return sku is not None and sku in cdn_notified

    # 分類：熱門款式 vs CDN 確認上架 vs 一般新品
    wishlist_products = [p for p in new_products if is_wishlist_match(p, wishlist)]
    cdn_confirmed_products = [p for p in new_products
                              if not is_wishlist_match(p, wishlist) and is_cdn_confirmed(p)]
    normal_products = [p for p in new_products
                       if not is_wishlist_match(p, wishlist) and not is_cdn_confirmed(p)]

    messages = []

    # 熱門款式命中 → 特別通知（排最前面）
    if wishlist_products:
        bubbles = []
        for p in wishlist_products[:5]:
            cdn_flag = is_cdn_confirmed(p)
            bubbles.append(build_flex_message(p, is_wishlist=True, is_cdn_confirmed=cdn_flag))

        messages.append({
            "type": "flex",
            "altText": f"🔥 熱門款式！{len(wishlist_products)} 件",
            "contents": {
                "type": "carousel",
                "contents": bubbles,
            },
        })

    # CDN 已預告 → 現在正式上架
    if cdn_confirmed_products:
        bubbles = []
        for p in cdn_confirmed_products[:5]:
            bubbles.append(build_flex_message(p, is_wishlist=False, is_cdn_confirmed=True))

        messages.append({
            "type": "flex",
            "altText": f"✅ 正式上架！{len(cdn_confirmed_products)} 件（CDN 已預告）",
            "contents": {
                "type": "carousel",
                "contents": bubbles,
            },
        })

    # 一般新品
    if normal_products:
        bubbles = []
        for p in normal_products[:5]:
            bubbles.append(build_flex_message(p, is_wishlist=False))

        messages.append({
            "type": "flex",
            "altText": f"🧡 愛馬仕新品 {len(normal_products)} 件",
            "contents": {
                "type": "carousel",
                "contents": bubbles,
            },
        })

    # 補貨統計 + 簽名
    footer = "蕭key來買喔～"
    stats = get_restock_stats()
    if stats:
        footer = f"{stats}\n\n蕭key來買喔～"
    messages.append({"type": "text", "text": footer})

    if len(messages) <= 1:
        return False

    # LINE 每次最多 5 則訊息
    messages = messages[:5]

    try:
        # Broadcast：所有加好友的人都會收到
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


def main():
    log("=" * 50)
    log("開始愛馬仕新品監控 v2")

    current_products = scrape_hermes()

    if not current_products:
        log("⚠️ 未爬到任何產品（可能被 DataDome 擋住）")
        return

    previous = load_previous_products()

    if previous:
        new_products = find_new_products(current_products, previous)
        if new_products:
            cdn_notified = load_cdn_notified_skus()
            cdn_count = sum(1 for p in new_products
                           if extract_sku(p.get("url", "")) in cdn_notified)
            log(f"🆕 發現 {len(new_products)} 件新品！"
                f"（其中 {cdn_count} 件 CDN 已預告）" if cdn_count else "")
            for p in new_products:
                sku = extract_sku(p.get("url", ""))
                cdn_tag = " [CDN✅]" if sku and sku in cdn_notified else ""
                log(f"  - {p.get('name', '未知')}{cdn_tag} | {p.get('url', '')}")

            # 記錄補貨歷史
            record_restock_history(new_products)

            # 發送通知
            send_line_notification(new_products)
        else:
            log("沒有新品上架")
    else:
        log(f"首次執行，記錄 {len(current_products)} 個現有產品（不發通知）")

    save_products(current_products)
    log("監控完成")
    log("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        log("測試模式：發送測試通知（含圖片 + 熱門款式）")
        test_products = [
            {
                "name": "Picotin Lock 18 手提包",
                "url": "https://www.hermes.com/tw/zh/product/picotin-lock-18%E6%89%8B%E6%8F%90%E5%8C%85-H056289CK18/",
                "image": "https://assets.hermes.com/is/image/hermesproduct/picotin-lock-18-bag--073055CK18-worn-1-0-0-800-800_g.jpg",
                "price": "NT$ 172,500",
            },
            {
                "name": "En Piste手拿包",
                "url": "https://www.hermes.com/tw/zh/product/en-piste%E6%89%8B%E6%8B%BF%E5%8C%85-H084948CP89/",
                "image": "https://assets.hermes.com/is/image/hermesproduct/en-piste%E6%89%8B%E6%8B%BF%E5%8C%85--084948CP89-front-wm-1-0-0-800-800_g.jpg",
                "price": "NT$ 252,300",
            },
        ]
        send_line_notification(test_products)
    else:
        main()
