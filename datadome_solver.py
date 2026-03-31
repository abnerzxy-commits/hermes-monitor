"""
DataDome CAPTCHA 自動解題模組（共用）
所有專案都可以 import 使用

使用方式：
    from datadome_solver import solve_datadome, setup_solver

    # 設定 2Captcha API Key（只需一次）
    setup_solver("your_2captcha_api_key")

    # 在 Playwright 頁面被 DataDome 擋住時呼叫
    success = solve_datadome(page, "https://target-url.com/page")
    if success:
        page.goto("https://target-url.com/page")  # 重新載入
"""

import os
import logging

logger = logging.getLogger("datadome_solver")

_api_key = None

try:
    from twocaptcha import TwoCaptcha
    HAS_2CAPTCHA = True
except ImportError:
    HAS_2CAPTCHA = False


def setup_solver(api_key: str | None = None):
    """設定 2Captcha API Key，也可以從環境變數 TWO_CAPTCHA_API_KEY 讀取"""
    global _api_key
    _api_key = api_key or os.getenv("TWO_CAPTCHA_API_KEY")
    if not _api_key:
        logger.warning("TWO_CAPTCHA_API_KEY 未設定")
    return bool(_api_key)


def get_api_key() -> str | None:
    global _api_key
    if not _api_key:
        _api_key = os.getenv("TWO_CAPTCHA_API_KEY")
    return _api_key


def is_datadome_blocked(page) -> bool:
    """檢查頁面是否被 DataDome 擋住"""
    try:
        html = page.content()
        return (
            "captcha-delivery.com" in html
            or "datadome" in html.lower() and "被禁止" in html
            or page.url and "geo.captcha-delivery.com" in page.url
        )
    except Exception:
        return False


def extract_captcha_url(page) -> str:
    """從被擋的頁面提取 CAPTCHA URL"""
    try:
        return page.evaluate("""
            () => {
                const iframe = document.querySelector('iframe[src*="captcha-delivery.com"], iframe[src*="datadome"]');
                if (iframe) return iframe.src;
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const text = s.textContent || s.innerText;
                    if (text.includes('captcha-delivery.com')) {
                        const match = text.match(/['"]([^'"]*captcha-delivery\\.com[^'"]*)['"]/);
                        if (match) return match[1];
                    }
                }
                return '';
            }
        """)
    except Exception:
        return ""


def solve_datadome(page, page_url: str) -> bool:
    """
    用 2Captcha 解 DataDome CAPTCHA

    Args:
        page: Playwright page 物件（已被 DataDome 擋住）
        page_url: 原始目標 URL

    Returns:
        True = 解題成功並已設定 cookie，可以重新載入頁面
        False = 解題失敗
    """
    api_key = get_api_key()
    if not HAS_2CAPTCHA:
        logger.error("2captcha-python 未安裝，請執行: pip install 2captcha-python")
        return False
    if not api_key:
        logger.error("TWO_CAPTCHA_API_KEY 未設定")
        return False

    try:
        # 1. 取得 CAPTCHA URL
        captcha_url = extract_captcha_url(page)
        if not captcha_url:
            logger.error("找不到 DataDome CAPTCHA iframe")
            return False

        logger.info(f"找到 CAPTCHA: {captcha_url[:80]}...")

        # 2. 取得 User-Agent
        user_agent = page.evaluate("() => navigator.userAgent")

        # 3. 取得 proxy（DataDome 要求 2Captcha 從同 IP 解題）
        proxy = os.getenv("CAPTCHA_PROXY", "")
        if not proxy:
            logger.error("CAPTCHA_PROXY 未設定（DataDome 需要 proxy）")
            return False

        # 解析 proxy：http://user:pass@host:port → user:pass@host:port
        from urllib.parse import urlparse
        parsed_proxy = urlparse(proxy)
        if parsed_proxy.username:
            proxy_for_2captcha = f"{parsed_proxy.username}:{parsed_proxy.password}@{parsed_proxy.hostname}:{parsed_proxy.port}"
        else:
            proxy_for_2captcha = f"{parsed_proxy.hostname}:{parsed_proxy.port}"

        # 4. 取得現有的 datadome cookie
        dd_cookie = ""
        for c in page.context.cookies():
            if c["name"] == "datadome":
                dd_cookie = c["value"]
                break

        # 5. 直接呼叫 2Captcha API
        import requests as req
        import time as t

        submit_params = {
            "key": api_key,
            "method": "datadome",
            "captcha_url": captcha_url,
            "pageurl": page_url,
            "userAgent": user_agent,
            "proxy": proxy_for_2captcha,
            "proxytype": "HTTP",
            "json": 1,
        }
        if dd_cookie:
            submit_params["dmd_cookie"] = f"datadome={dd_cookie}"

        logger.info(f"2Captcha 送出: pageurl={page_url}, proxy={proxy_for_2captcha[:30]}..., captcha_url={captcha_url[:80]}...")

        submit_resp = req.post("https://2captcha.com/in.php", data=submit_params, timeout=30)
        submit_data = submit_resp.json()
        logger.info(f"2Captcha submit: {submit_data}")

        if submit_data.get("status") != 1:
            logger.error(f"2Captcha 提交失敗: {submit_data.get('request', 'unknown')}")
            return False

        task_id = submit_data["request"]

        # 輪詢結果（最多等 120 秒）
        for _ in range(24):
            t.sleep(5)
            result_resp = req.get("https://2captcha.com/res.php", params={
                "key": api_key,
                "action": "get",
                "id": task_id,
                "json": 1,
            }, timeout=15)
            result_data = result_resp.json()

            if result_data.get("status") == 1:
                datadome_cookie = result_data.get("request", "")
                logger.info(f"2Captcha 解題成功！cookie: {datadome_cookie[:50]}...")

                # 設定新的 datadome cookie
                domain = urlparse(page_url).hostname
                if domain:
                    domain = "." + domain.lstrip(".")

                page.context.add_cookies([{
                    "name": "datadome",
                    "value": datadome_cookie,
                    "domain": domain or ".hermes.com",
                    "path": "/",
                }])
                return True

            elif result_data.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                logger.error(f"2Captcha 失敗: {result_data.get('request', 'unknown')}")
                return False

        logger.error("2Captcha 解題逾時（120s）")
        return False

    except Exception as e:
        logger.error(f"2Captcha 解題例外: {e}")
        return False


def with_datadome_bypass(page, url: str, max_retries: int = 2, **goto_kwargs) -> bool:
    """
    便利函式：載入頁面，如果被 DataDome 擋住就自動解題重試

    Args:
        page: Playwright page 物件
        url: 目標 URL
        max_retries: 最多解題重試次數
        **goto_kwargs: 傳給 page.goto 的額外參數

    Returns:
        True = 頁面成功載入
        False = 最終仍然被擋

    Usage:
        page = context.new_page()
        if with_datadome_bypass(page, "https://example.com", timeout=60000):
            # 頁面成功載入，可以抓資料
            html = page.content()
    """
    goto_kwargs.setdefault("wait_until", "networkidle")
    goto_kwargs.setdefault("timeout", 60000)

    resp = page.goto(url, **goto_kwargs)
    status = resp.status if resp else 0

    if status != 403:
        return status == 200

    for attempt in range(max_retries):
        logger.info(f"DataDome 擋住，嘗試解題 ({attempt + 1}/{max_retries})...")
        if solve_datadome(page, url):
            resp = page.goto(url, **goto_kwargs)
            status = resp.status if resp else 0
            if status != 403:
                return status == 200

    return False
