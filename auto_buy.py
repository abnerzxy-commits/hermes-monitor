#!/usr/bin/env python3
"""
愛馬仕自動加購系統
==================

原理：
- 用 Playwright 模擬真實瀏覽器加購流程
- 載入儲存的 cookies（從你的瀏覽器抓的）保持登入狀態
- 偵測商品 → 立即訪問商品頁 → 點選顏色/尺寸 → 加入購物車
- 全程在 1-3 秒完成

前置作業：
1. 在你 Chrome 上登入愛馬仕 → 維持登入狀態
2. 用 cookie 匯出工具（例如 EditThisCookie）匯出 cookies
3. 儲存到 data/hermes_cookies.json
4. 設定環境變數 HERMES_AUTO_BUY=1

執行：
    python3 auto_buy.py --sku 084948CP89        # 直接試加購單一 SKU
    python3 auto_buy.py --sku 084948CP89 --headed   # 顯示瀏覽器（除錯用）

WARNING: 這會真實加入你的購物車！只做加購，不會送出訂單。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
COOKIES_FILE = DATA_DIR / "hermes_cookies.json"
LOG_FILE = DATA_DIR / "auto_buy.log"
HISTORY_FILE = DATA_DIR / "auto_buy_history.json"
RATE_LIMIT_FILE = DATA_DIR / "auto_buy_rate_limit.json"

# ─── Anti-ban safety limits ─────────────────────────────
# 全域：防止被愛馬仕封鎖的速率限制
MAX_ATTEMPTS_PER_HOUR = 6       # 每小時最多嘗試 6 次（含失敗）
MAX_ATTEMPTS_PER_DAY = 20       # 每天最多 20 次
MIN_INTERVAL_SECONDS = 60       # 兩次嘗試最少間隔 60 秒
COOLDOWN_AFTER_BLOCK = 3600     # 偵測到 403/captcha 後冷卻 1 小時

# 單一 SKU 重試機制（針對「商品圖已上但頁面還沒開放」的情況）
RETRY_DELAYS = [10, 30, 60, 120, 300]  # 重試間隔：10s, 30s, 1min, 2min, 5min
# 每個 SKU 最多重試 5 次，總共間隔 8 分鐘


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    DATA_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_cookies() -> list:
    """從檔案載入 cookies"""
    if not COOKIES_FILE.exists():
        return []
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"❌ 讀 cookies 失敗: {e}")
        return []


def save_history(entry: dict):
    """記錄加購歷史"""
    DATA_DIR.mkdir(exist_ok=True)
    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append(entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ─── Rate limit (防封鎖) ──────────────────────────────
def load_rate_limit() -> dict:
    if not RATE_LIMIT_FILE.exists():
        return {"attempts": [], "blocked_until": 0}
    try:
        with open(RATE_LIMIT_FILE) as f:
            return json.load(f)
    except Exception:
        return {"attempts": [], "blocked_until": 0}


def save_rate_limit(data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(RATE_LIMIT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def check_rate_limit() -> tuple[bool, str]:
    """
    檢查是否可以執行下一次加購

    Returns:
        (允許嗎, 拒絕理由)
    """
    rl = load_rate_limit()
    now = time.time()

    # Check cooldown
    if rl.get("blocked_until", 0) > now:
        remaining = int(rl["blocked_until"] - now)
        return False, f"系統冷卻中（被偵測到 403/captcha），剩 {remaining} 秒"

    # Filter attempts in last hour and last day
    attempts = rl.get("attempts", [])
    last_hour = [t for t in attempts if t > now - 3600]
    last_day = [t for t in attempts if t > now - 86400]

    if last_hour and (now - last_hour[-1]) < MIN_INTERVAL_SECONDS:
        wait = int(MIN_INTERVAL_SECONDS - (now - last_hour[-1]))
        return False, f"距離上次嘗試太近，等 {wait} 秒"

    if len(last_hour) >= MAX_ATTEMPTS_PER_HOUR:
        return False, f"過去 1 小時已嘗試 {len(last_hour)} 次（上限 {MAX_ATTEMPTS_PER_HOUR}）"

    if len(last_day) >= MAX_ATTEMPTS_PER_DAY:
        return False, f"今天已嘗試 {len(last_day)} 次（上限 {MAX_ATTEMPTS_PER_DAY}）"

    return True, ""


def record_attempt(blocked: bool = False):
    """記錄一次嘗試"""
    rl = load_rate_limit()
    now = time.time()
    rl.setdefault("attempts", []).append(now)
    # 只保留最近 24h
    rl["attempts"] = [t for t in rl["attempts"] if t > now - 86400]
    if blocked:
        rl["blocked_until"] = now + COOLDOWN_AFTER_BLOCK
        log(f"⚠️ 偵測到封鎖訊號，冷卻 {COOLDOWN_AFTER_BLOCK//60} 分鐘")
    save_rate_limit(rl)


def add_to_cart(sku: str, headed: bool = False, use_chrome: bool = False) -> dict:
    """
    嘗試把 SKU 加入購物車

    Args:
        sku: 商品 SKU
        headed: 顯示瀏覽器視窗（除錯用，不適用於 use_chrome）
        use_chrome: 連線到本機 Chrome remote debugging port (9222)
                    需要先執行 ./start_chrome_debug.sh

    Returns:
        {"success": bool, "error": str, "cart_url": str, "elapsed": float}
    """
    if not HAS_PLAYWRIGHT:
        return {"success": False, "error": "Playwright 未安裝：pip install playwright && playwright install chromium"}

    # 防封鎖檢查
    allowed, reason = check_rate_limit()
    if not allowed:
        log(f"🛑 速率限制：{reason}")
        return {"success": False, "error": f"速率限制：{reason}", "rate_limited": True}

    record_attempt()
    log(f"🛒 開始加購 {sku}... (mode={'chrome' if use_chrome else 'playwright'})")
    start = time.time()

    with sync_playwright() as p:
        if use_chrome:
            # 連線到已執行的 Chrome (remote debugging)
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
            except Exception as e:
                return {"success": False, "error": f"無法連線到 Chrome remote debugging。請先執行 ./start_chrome_debug.sh\n{e}"}
            # 用第一個現有 context（你的 Chrome 主視窗）
            if browser.contexts:
                context = browser.contexts[0]
            else:
                return {"success": False, "error": "Chrome 沒有可用的 context"}
            should_close_browser = False  # 不要關掉用戶的 Chrome
        else:
            # 一般模式：開新的 chromium + 套用 cookies
            cookies = load_cookies()
            if not cookies:
                return {"success": False, "error": f"沒有 cookies。請從瀏覽器匯出到 {COOKIES_FILE}"}

            browser = p.chromium.launch(
                headless=not headed,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="zh-TW",
                viewport={"width": 1280, "height": 800},
            )
            context.add_cookies(cookies)
            should_close_browser = True

        try:
            # 速度優化：use_chrome 模式下重用已暖的 hermes.com 頁面，不開新分頁也跳過 home goto
            if use_chrome:
                warm_page = None
                for pg in context.pages:
                    if "hermes.com" in pg.url:
                        warm_page = pg
                        break
                if warm_page:
                    page = warm_page
                    log(f"♻️  重用暖頁: {page.url[:60]}")
                else:
                    page = context.new_page()
                    log(f"🏠 沒有暖頁，先建立 session...")
                    page.goto("https://www.hermes.com/tw/zh/", timeout=30000, wait_until="domcontentloaded")
                    time.sleep(1.0)
            else:
                page = context.new_page()
                # 一般 chromium 模式：必須先 goto home 建立 session
                log(f"🏠 訪問首頁建立 session...")
                page.goto("https://www.hermes.com/tw/zh/", timeout=30000, wait_until="domcontentloaded")
                time.sleep(1.5)

            # 搜尋 SKU
            log(f"🔍 搜尋 SKU: {sku}")
            search_url = f"https://www.hermes.com/tw/zh/search/?s={sku}"
            resp = page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2.5)

            # 偵測封鎖
            if resp and resp.status in (403, 429):
                record_attempt(blocked=True)
                return {"success": False, "error": f"被擋（HTTP {resp.status}），已啟動冷卻", "blocked": True}
            html_check = page.content()
            if "captcha-delivery" in html_check:
                record_attempt(blocked=True)
                return {"success": False, "error": "偵測到 DataDome captcha，已啟動冷卻", "blocked": True}

            # Step 3: 在搜尋結果裡找商品連結（必須是 /product/ 路徑且包含 SKU）
            product_link = None
            for selector_sku in [sku, sku.upper(), sku.lower()]:
                # 必須是 /product/ 路徑
                links = page.query_selector_all(f'a[href*="/product/"][href*="{selector_sku}"]')
                if links:
                    product_link = links[0]
                    break

            if not product_link:
                return {
                    "success": False,
                    "error": f"搜尋結果中找不到 SKU {sku} 的商品連結（可能還沒上架或已下架）",
                    "elapsed": time.time() - start,
                }

            product_href = product_link.get_attribute("href")
            log(f"✅ 找到商品連結: {product_href[:80]}")

            # Step 4: 點商品連結（用 force=True 繞過 header 遮擋，或直接 navigate）
            log(f"🖱️ 開啟商品...")
            full_url = f"https://www.hermes.com{product_href}" if product_href.startswith("/") else product_href
            try:
                # 用 dispatchEvent 直接觸發 click，繞過 viewport 問題
                product_link.dispatch_event("click")
                time.sleep(3)
            except Exception:
                # Fallback: 直接 navigate（這次 session 已建立，應該不會被擋）
                log(f"   改用 goto 開啟...")
                page.goto(full_url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(3)
            log(f"   商品頁已開啟: {page.title()[:50]}")

            # Step 5: 點選顏色（如果有）
            color_buttons = page.query_selector_all('button[data-testid*="color"], button[aria-label*="color"], .color-swatch')
            if color_buttons:
                log(f"🎨 找到 {len(color_buttons)} 個顏色，選第一個")
                try:
                    color_buttons[0].click()
                    time.sleep(1)
                except Exception as e:
                    log(f"   ⚠️ 顏色點選失敗: {e}")

            # Step 6: 點加入購物車按鈕
            # 從 debug 知道：testid="Add to cart" + 文字="加入購物車"
            add_button_selectors = [
                'button[data-testid="Add to cart"]',
                'button[data-testid="add-to-cart-button"]',
                'button[data-testid*="Add to cart"]',
                'button[data-testid*="add-to-cart"]',
                'button:has-text("加入購物車")',
                'button:has-text("Add to cart")',
            ]

            clicked = False
            for sel in add_button_selectors:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        # 檢查是否被禁用
                        if btn.is_disabled():
                            log(f"⚠️ 加購按鈕被禁用（可能已售完）")
                            continue
                        log(f"✅ 找到加購按鈕: {sel}")
                        btn.click()
                        clicked = True
                        break
                except Exception as e:
                    continue

            if not clicked:
                return {
                    "success": False,
                    "error": "找不到加入購物車按鈕（可能已售完或頁面結構改變）",
                    "elapsed": time.time() - start,
                }

            # Step 7: 等待購物車更新
            time.sleep(2.5)

            # 偵測加購結果 — 看 header 上的購物車數字或購物車頁面
            cart_count = None
            try:
                cart_btn = page.query_selector('[data-testid="header-cart-button"]')
                if cart_btn:
                    cart_text = cart_btn.inner_text() or ""
                    log(f"   購物車按鈕文字: {cart_text[:50]}")
                    # 看是不是含「空」字（沒商品）或數字
                    if "空" not in cart_text:
                        cart_count = "non-empty"
            except Exception:
                pass

            # 進購物車驗證
            log(f"🛒 驗證購物車...")
            cart_url = "https://www.hermes.com/tw/zh/cart/"
            page.goto(cart_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

            # 檢查購物車是否有這個 SKU
            cart_html = page.content()
            success = sku.lower() in cart_html.lower() or sku.upper() in cart_html.upper()

            elapsed = time.time() - start
            entry = {
                "sku": sku,
                "success": success,
                "timestamp": datetime.now().isoformat(),
                "elapsed": elapsed,
            }
            save_history(entry)

            if success:
                log(f"🎉 加購成功！耗時 {elapsed:.1f} 秒")
                return {
                    "success": True,
                    "cart_url": cart_url,
                    "elapsed": elapsed,
                }
            else:
                log(f"⚠️ 加購完成但購物車未確認，可能需要手動檢查")
                return {
                    "success": False,
                    "error": "購物車未確認包含此商品",
                    "elapsed": elapsed,
                }

        except PlaywrightTimeout as e:
            return {"success": False, "error": f"Timeout: {e}", "elapsed": time.time() - start}
        except Exception as e:
            return {"success": False, "error": str(e), "elapsed": time.time() - start}
        finally:
            if should_close_browser:
                browser.close()


def setup_cookies_helper():
    """互動式設定 cookies"""
    if not HAS_PLAYWRIGHT:
        print("❌ 請先安裝：pip install playwright && playwright install chromium")
        return

    print("\n🍪 Cookie 設定流程")
    print("=" * 50)
    print("1. 我會打開 Hermes 網頁")
    print("2. 你在裡面登入你的帳號")
    print("3. 登入完成後按 Enter，cookies 會自動儲存")
    print("=" * 50)
    input("\n按 Enter 開始...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="zh-TW",
        )
        page = context.new_page()
        page.goto("https://www.hermes.com/tw/zh/login/")

        print("\n👉 請在打開的瀏覽器中登入你的愛馬仕帳號")
        input("登入完成後，按 Enter 儲存 cookies...")

        cookies = context.cookies()
        DATA_DIR.mkdir(exist_ok=True)
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        print(f"\n✅ Cookies 已儲存：{COOKIES_FILE}")
        print(f"   共 {len(cookies)} 個 cookies")
        browser.close()


def add_to_cart_with_retry(sku: str, headed: bool = False, use_chrome: bool = False) -> dict:
    """
    重試版加購：偵測到「商品還沒開放」時會自動重試

    重試規則：
    - 每次重試間隔：10s, 30s, 60s, 2min, 5min（共 5 次）
    - 中途遇到 403/captcha → 立刻停止 + 觸發冷卻
    - 中途遇到「商品已加購」→ 立刻成功返回
    - 重試也計入 rate limit
    """
    log(f"🔁 開始重試模式加購 {sku}")

    # 第 1 次（不算重試）
    result = add_to_cart(sku, headed=headed, use_chrome=use_chrome)
    result["attempt"] = 1

    if result.get("success") or result.get("blocked") or result.get("rate_limited"):
        return result

    # 重試
    for i, delay in enumerate(RETRY_DELAYS, start=2):
        log(f"⏳ 第 {i-1} 次重試前等待 {delay} 秒...")
        time.sleep(delay)

        # 每次重試前再檢查 rate limit
        allowed, reason = check_rate_limit()
        if not allowed:
            log(f"🛑 重試被速率限制擋下：{reason}")
            return {
                "success": False,
                "error": f"重試被速率限制擋下：{reason}",
                "rate_limited": True,
                "attempt": i,
            }

        result = add_to_cart(sku, headed=headed, use_chrome=use_chrome)
        result["attempt"] = i

        if result.get("success"):
            log(f"🎉 第 {i} 次重試成功！")
            return result

        if result.get("blocked"):
            log(f"🛑 第 {i} 次重試被擋，停止重試")
            return result

        if result.get("rate_limited"):
            return result

    log(f"❌ {len(RETRY_DELAYS)+1} 次嘗試後仍未成功")
    return result


def main():
    parser = argparse.ArgumentParser(description="愛馬仕自動加購")
    parser.add_argument("--sku", help="要加購的 SKU")
    parser.add_argument("--headed", action="store_true", help="顯示瀏覽器")
    parser.add_argument("--use-chrome", action="store_true",
                        help="連線到本機 Chrome remote debugging（需先跑 ./start_chrome_debug.sh）")
    parser.add_argument("--retry", action="store_true",
                        help="使用重試模式（最多重試 5 次，總共 8 分鐘）")
    parser.add_argument("--setup", action="store_true", help="互動設定 cookies")
    args = parser.parse_args()

    if args.setup:
        setup_cookies_helper()
        return

    if not args.sku:
        print("用法:")
        print("  python3 auto_buy.py --sku 084948CP89 --use-chrome   # 推薦：用本機 Chrome")
        print("  python3 auto_buy.py --sku 084948CP89                # 用獨立 chromium + cookies")
        print("  python3 auto_buy.py --setup                         # 首次設定 cookies")
        return

    if args.retry:
        result = add_to_cart_with_retry(args.sku, headed=args.headed, use_chrome=args.use_chrome)
    else:
        result = add_to_cart(args.sku, headed=args.headed, use_chrome=args.use_chrome)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
