#!/usr/bin/env python3
"""
商品資訊 enrichment
==================
給定 SKU，從 hermes.com 抓取：
- 商品標題
- 顏色（內含於 title 屬性）
- 完整商品 URL
- 大圖 URL（用已知 CDN URL pattern）

注意：只抓 search 結果頁，不直接 goto product page
（product page 會被 DataDome 擋住，需要 home → search → click 流程）
"""
import re
import time

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


CDN_BASE = "https://assets.hermes.com/is/image/hermesproduct"


def enrich_sku(sku: str, timeout_seconds: int = 12) -> dict:
    """
    從 hermes.com search 結果抓取 SKU 的商品資訊
    回傳 dict：{title, color, product_url, image_url, success}
    """
    result = {
        "sku": sku,
        "title": None,
        "color": None,
        "product_url": None,
        "image_url": f"{CDN_BASE}/{sku}_front_wm_1?wid=800&hei=800",
        "success": False,
    }

    if not HAS_PLAYWRIGHT:
        return result

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
            except Exception:
                return result
            ctx = browser.contexts[0]
            page = ctx.new_page()
            try:
                search_url = f"https://www.hermes.com/tw/zh/search/?s={sku}"
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                except Exception:
                    pass
                time.sleep(4)  # 等 hermes JS 處理 search

                # 找 product link
                try:
                    link = page.query_selector(f'a[href*="/product/"][href*="{sku}"]')
                except Exception:
                    return result
                if not link:
                    return result

                # href
                href = link.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.hermes.com" + href
                result["product_url"] = href

                # title 屬性帶有「商品名, 顏色」格式
                title_attr = link.get_attribute("title") or ""
                if title_attr:
                    # 切出商品名 + 顏色
                    parts = [p.strip() for p in title_attr.split(",")]
                    if len(parts) >= 1:
                        result["title"] = parts[0][:100]
                    if len(parts) >= 2:
                        result["color"] = ",".join(parts[1:])[:50]

                # fallback to inner_text 或 img alt
                if not result["title"]:
                    inner = (link.inner_text() or "").strip()
                    if inner:
                        result["title"] = inner.split("\n")[0][:100]

                if not result["title"]:
                    img = link.query_selector("img")
                    if img:
                        alt = img.get_attribute("alt") or ""
                        if alt:
                            result["title"] = alt.split(",")[0].strip()[:100]

                result["success"] = bool(result["title"])
            finally:
                page.close()
    except Exception:
        pass

    return result


if __name__ == "__main__":
    import sys, json
    sku = sys.argv[1] if len(sys.argv) > 1 else "084948CP89"
    print(json.dumps(enrich_sku(sku), ensure_ascii=False, indent=2))
