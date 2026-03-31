#!/usr/bin/env python3
"""
愛馬仕新品包包監控（雲端版）
使用 Playwright，適合 GitHub Actions
"""

import json
import os
import sys
import hashlib
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

HERMES_URLS = [
    "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
]

DATA_DIR = Path(__file__).parent / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"
LOG_FILE = DATA_DIR / "monitor.log"


def log(msg: str):
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def scrape_hermes() -> list[dict]:
    """用 Playwright 爬愛馬仕"""
    all_products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
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

        for url in HERMES_URLS:
            log(f"正在爬取: {url}")
            try:
                page = context.new_page()
                resp = page.goto(url, wait_until="networkidle", timeout=60000)

                status = resp.status if resp else 0
                log(f"  HTTP 狀態: {status}")

                if status == 403:
                    log("  ⚠️ 被 DataDome 擋住 (403)")
                    page.close()
                    continue

                # 等待頁面載入
                page.wait_for_timeout(5000)

                # 滾動載入更多
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)

                # 抓取產品
                products_data = page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/product/"]');
                        const products = [];
                        const seen = {};
                        for (const a of links) {
                            const href = a.href;
                            if (seen[href]) continue;
                            seen[href] = true;
                            const name = a.textContent.trim().replace(/\\s+/g, ' ').substring(0, 200);
                            let img = '';
                            const imgEl = a.querySelector('img');
                            if (imgEl) img = imgEl.src || imgEl.dataset?.src || '';
                            products.push({name, url: href, image: img});
                        }
                        return products;
                    }
                """)

                log(f"  找到 {len(products_data)} 個產品")

                for pd in products_data:
                    product_id = hashlib.md5(pd["url"].encode()).hexdigest()[:12]
                    product = {
                        "id": product_id,
                        "name": pd.get("name", ""),
                        "url": pd.get("url", ""),
                        "image": pd.get("image", ""),
                        "first_seen": datetime.now().isoformat(),
                    }
                    if not any(ep["id"] == product_id for ep in all_products):
                        all_products.append(product)

                page.close()

            except Exception as e:
                log(f"  爬取失敗: {e}")
                continue

        browser.close()

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
    if not LINE_TOKEN or LINE_TOKEN == "your_token_here":
        log("⚠️ LINE Token 未設定")
        return False
    if not LINE_USER_ID or LINE_USER_ID == "your_user_id_here":
        log("⚠️ LINE User ID 未設定")
        return False

    msg_lines = [f"🧡 愛馬仕新品上架！共 {len(new_products)} 件\n"]
    for i, p in enumerate(new_products[:10], 1):
        msg_lines.append(f"{i}. {p.get('name', '未知品名')}")
        if p.get("url"):
            msg_lines.append(f"   🔗 {p['url']}")
        msg_lines.append("")

    if len(new_products) > 10:
        msg_lines.append(f"... 還有 {len(new_products) - 10} 件")
    msg_lines.append(f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_TOKEN}",
            },
            json={
                "to": LINE_USER_ID,
                "messages": [{"type": "text", "text": "\n".join(msg_lines)}],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log(f"✅ LINE 通知成功，共 {len(new_products)} 件新品")
            return True
        else:
            log(f"❌ LINE 通知失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        log(f"❌ LINE 通知例外: {e}")
        return False


def main():
    log("=" * 50)
    log("開始愛馬仕新品監控（雲端版）")

    current_products = scrape_hermes()

    if not current_products:
        log("⚠️ 未爬到任何產品（可能被 DataDome 擋住）")
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
