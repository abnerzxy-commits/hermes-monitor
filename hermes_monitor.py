#!/usr/bin/env python3
"""
愛馬仕新品包包監控
透過 AppleScript 控制使用者的 Chrome 來爬，不會被 DataDome 擋
每小時執行一次，有新品透過 LINE Bot 通知
"""

import json
import os
import subprocess
import sys
import hashlib
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# 要監控的包包頁面 URL
HERMES_URLS = [
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
]

DATA_DIR = Path(__file__).parent / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"
LOG_FILE = DATA_DIR / "monitor.log"

# 從 Chrome 分頁抓取產品資料的 JavaScript
EXTRACT_JS = r"""
(function() {
    var links = document.querySelectorAll('a[href*="/product/"]');
    var products = [];
    var seen = {};
    for (var j = 0; j < links.length; j++) {
        var a = links[j];
        var href = a.href;
        if (seen[href]) continue;
        seen[href] = true;
        var name = a.textContent.trim().replace(/\s+/g, ' ').substring(0, 200);
        var img = '';
        var imgEl = a.querySelector('img');
        if (!imgEl) {
            var parent = a.closest('[class*=grid], [class*=product], li');
            if (parent) imgEl = parent.querySelector('img');
        }
        if (imgEl) img = imgEl.src || imgEl.dataset.src || '';
        products.push({name: name, url: href, image: img});
    }
    return JSON.stringify(products);
})()
"""


def log(msg: str):
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_chrome_running():
    """確保 Chrome 正在運行"""
    result = subprocess.run(
        ["pgrep", "-x", "Google Chrome"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log("啟動 Chrome...")
        subprocess.run(["open", "-a", "Google Chrome"], check=True)
        import time
        time.sleep(3)


def chrome_navigate(url: str) -> bool:
    """用 AppleScript 在 Chrome 開啟指定 URL"""
    script = f'''
    tell application "Google Chrome"
        activate
        set found to false
        repeat with w in windows
            repeat with t in tabs of w
                if URL of t contains "hermes.com" then
                    set URL of t to "{url}"
                    set found to true
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat
        if not found then
            tell front window
                make new tab with properties {{URL:"{url}"}}
            end tell
        end if
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    return result.returncode == 0


def chrome_extract_products() -> list[dict]:
    """用 AppleScript 從 Chrome 的 Hermès 分頁抓取產品資料"""
    # 轉義 JS 給 AppleScript 用
    js_escaped = EXTRACT_JS.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')

    script = f'''
    tell application "Google Chrome"
        set tabCount to count of tabs of front window
        repeat with i from 1 to tabCount
            set tabUrl to URL of tab i of front window
            if tabUrl contains "hermes.com" then
                set result to execute tab i of front window javascript "{js_escaped}"
                return result
            end if
        end repeat
        return "NO_TAB"
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0 or result.stdout.strip() == "NO_TAB":
        log(f"AppleScript 失敗: {result.stderr.strip()}")
        return []

    try:
        products_raw = json.loads(result.stdout.strip())
        products = []
        for p in products_raw:
            product_id = hashlib.md5(p["url"].encode()).hexdigest()[:12]
            products.append({
                "id": product_id,
                "name": p.get("name", ""),
                "url": p.get("url", ""),
                "image": p.get("image", ""),
                "first_seen": datetime.now().isoformat(),
            })
        return products
    except (json.JSONDecodeError, KeyError) as e:
        log(f"JSON 解析失敗: {e}")
        return []


def scrape_hermes() -> list[dict]:
    """透過 Chrome + AppleScript 爬愛馬仕包包頁面"""
    import time

    ensure_chrome_running()
    all_products = []

    for url in HERMES_URLS:
        log(f"正在爬取: {url}")

        if not chrome_navigate(url):
            log("  導航失敗")
            continue

        # 等待頁面載入
        time.sleep(8)

        # 滾動頁面載入更多產品
        scroll_script = '''
        tell application "Google Chrome"
            repeat with i from 1 to (count of tabs of front window)
                if URL of tab i of front window contains "hermes.com" then
                    execute tab i of front window javascript "window.scrollTo(0, document.body.scrollHeight)"
                    exit repeat
                end if
            end repeat
        end tell
        '''
        for _ in range(3):
            subprocess.run(["osascript", "-e", scroll_script],
                         capture_output=True, timeout=10)
            time.sleep(2)

        # 抓取產品
        products = chrome_extract_products()
        log(f"  找到 {len(products)} 個產品")

        for p in products:
            if not any(ep["id"] == p["id"] for ep in all_products):
                all_products.append(p)

    log(f"總共找到 {len(all_products)} 個產品")
    return all_products


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


def send_line_notification(new_products: list[dict]):
    """發送 LINE Flex Message（圖片 + 立即購買按鈕）"""
    if not LINE_TOKEN or LINE_TOKEN == "your_token_here":
        log("⚠️ LINE Token 未設定，跳過通知")
        return False
    if not LINE_USER_ID or LINE_USER_ID == "your_user_id_here":
        log("⚠️ LINE User ID 未設定，跳過通知")
        return False

    # 載入許願清單
    wishlist = []
    wishlist_file = DATA_DIR / "wishlist.json"
    if wishlist_file.exists():
        with open(wishlist_file, "r", encoding="utf-8") as f:
            wishlist = json.load(f)

    def is_wishlist(p):
        text = f"{p.get('name', '')} {p.get('url', '')}".lower()
        return any(w.lower() in text for w in wishlist)

    def build_bubble(p, is_wish):
        tag = "⭐ 許願清單命中！" if is_wish else "🆕 新品上架"
        tag_color = "#FF0000" if is_wish else "#FF6B00"
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": tag, "weight": "bold", "color": tag_color, "size": "sm"},
                    {"type": "text", "text": p.get("name", "未知"), "weight": "bold", "size": "lg", "wrap": True, "margin": "md"},
                ],
                "paddingAll": "15px",
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "action": {"type": "uri", "label": "立即購買 🛒", "uri": p.get("url", "https://www.hermes.com/tw/zh/")}, "style": "primary", "color": "#FF6B00"},
                ],
                "paddingAll": "10px",
            },
        }
        img = p.get("image", "")
        if img and img.startswith("http"):
            bubble["hero"] = {"type": "image", "url": img, "size": "full", "aspectRatio": "4:3", "aspectMode": "cover"}
        price = p.get("price", "")
        if price:
            bubble["body"]["contents"].append({"type": "text", "text": f"💰 {price}", "size": "md", "color": "#333333", "margin": "sm"})
        return bubble

    wish_products = [p for p in new_products if is_wishlist(p)]
    normal_products = [p for p in new_products if not is_wishlist(p)]

    messages = []
    if wish_products:
        messages.append({
            "type": "flex",
            "altText": f"⭐ 許願清單命中！{len(wish_products)} 件",
            "contents": {"type": "carousel", "contents": [build_bubble(p, True) for p in wish_products[:5]]},
        })
    if normal_products:
        messages.append({
            "type": "flex",
            "altText": f"🧡 愛馬仕新品 {len(normal_products)} 件",
            "contents": {"type": "carousel", "contents": [build_bubble(p, False) for p in normal_products[:5]]},
        })

    if not messages:
        return False

    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_TOKEN}",
            },
            json={"to": LINE_USER_ID, "messages": messages[:5]},
            timeout=10,
        )
        if resp.status_code == 200:
            log(f"✅ LINE 通知成功（許願 {len(wish_products)} + 一般 {len(normal_products)}）")
            return True
        else:
            log(f"❌ LINE 通知失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log(f"❌ LINE 通知例外: {e}")
        return False


def main():
    log("=" * 50)
    log("開始愛馬仕新品監控")

    current_products = scrape_hermes()

    if not current_products:
        log("⚠️ 未爬到任何產品")
        return

    previous = load_previous_products()

    if previous:
        new_products = find_new_products(current_products, previous)
        if new_products:
            log(f"🆕 發現 {len(new_products)} 件新品！")
            for p in new_products:
                log(f"  - {p.get('name', '未知')} | {p.get('url', '')}")
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
        log("測試模式：發送測試通知")
        send_line_notification([{
            "name": "Birkin 25 測試",
            "url": "https://www.hermes.com/tw/zh/product/test/",
        }])
    else:
        main()
