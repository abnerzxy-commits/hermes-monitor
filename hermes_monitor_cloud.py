#!/usr/bin/env python3
"""
愛馬仕新品包包監控（雲端版）
使用 undetected-chromedriver，不依賴本機 Chrome session
"""

import json
import os
import ssl
import sys
import hashlib
import time
from datetime import datetime
from pathlib import Path

import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
ssl._create_default_https_context = ssl._create_unverified_context

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # GitHub Actions 用環境變數，不需要 dotenv

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
    """用 undetected-chromedriver 爬愛馬仕"""
    all_products = []

    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=zh-TW")

    driver = None
    try:
        driver = uc.Chrome(options=options)

        for url in HERMES_URLS:
            log(f"正在爬取: {url}")
            driver.get(url)

            # 等待頁面載入
            time.sleep(10)

            # 檢查是否被擋
            page_source = driver.page_source
            if "被禁止" in page_source or "captcha" in page_source.lower():
                log("⚠️ 被 DataDome 擋住，可能需要換 IP")
                continue

            # 滾動載入更多
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            # 抓取產品連結
            product_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/product/"]')
            log(f"  找到 {len(product_links)} 個產品連結")

            seen = set()
            for link in product_links:
                try:
                    href = link.get_attribute("href")
                    if not href or href in seen:
                        continue
                    seen.add(href)

                    name = link.text.strip().replace("\n", " ")[:200]
                    img = ""
                    try:
                        img_el = link.find_element(By.CSS_SELECTOR, "img")
                        img = img_el.get_attribute("src") or ""
                    except Exception:
                        pass

                    product_id = hashlib.md5(href.encode()).hexdigest()[:12]
                    all_products.append({
                        "id": product_id,
                        "name": name,
                        "url": href,
                        "image": img,
                        "first_seen": datetime.now().isoformat(),
                    })
                except Exception:
                    continue

    except Exception as e:
        log(f"爬蟲錯誤: {e}")
    finally:
        if driver:
            driver.quit()

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
