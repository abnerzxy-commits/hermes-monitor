#!/usr/bin/env python3
"""
SKU 預測器（輕量版）
====================
分析現有 SKU 編號規律，預測未來可能的 SKU

SKU 格式：MMMMMM LL CC
  - MMMMMM: 6 位數模型編號（例如 084948 = En Piste 手拿包）
  - LL:     2 字母皮料/材質代碼（CP, CK, CC, CA...）
  - CC:     2 字符顏色代碼（37, 89, AA, P0...）

預測策略（保守，候選空間 < 5000）：
1. 對每個模型找它已出現的 (皮料, 顏色) 組合
2. 對該模型擴展：(同皮料 × 同模型沒出現的顏色)
3. 限制：只擴展「全資料集中至少出現 3 次以上」的顏色

然後把候選餵給 CDN probe（並行）
- 200 = 該 SKU 在 CDN 存在 → 加入 watchlist
- 404 = 不存在 → 丟掉

執行：
    python3 sku_predictor.py             # 全量預測 + 探測
    python3 sku_predictor.py --dry-run   # 只生成候選不探測
    python3 sku_predictor.py --max 1000  # 限制候選數量
"""
import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

WATCHLIST_FILE = DATA_DIR / "sku_watchlist.json"
SCRAPED_FILE = DATA_DIR / "scraped_skus.json"
STATE_FILE = DATA_DIR / "cdn_state.json"
PREDICTOR_FILE = DATA_DIR / "predicted_skus.json"
LOG_FILE = DATA_DIR / "predictor.log"

CDN_PROBE_URL = "https://assets.hermes.com/is/image/hermesproduct/{sku}_front_wm_1?wid=100&hei=100"

SKU_PARTS = re.compile(r"^(\d{6})([A-Z]{2})([A-Z0-9]{2})$")


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_known_skus() -> set[str]:
    """從 scraped + watchlist 載入已知 SKU"""
    known = set()
    if SCRAPED_FILE.exists():
        try:
            data = json.loads(SCRAPED_FILE.read_text())
            known |= set(data.get("skus", []))
        except Exception:
            pass
    if WATCHLIST_FILE.exists():
        try:
            known |= set(json.loads(WATCHLIST_FILE.read_text()))
        except Exception:
            pass
    return known


def parse_sku(sku: str):
    """解析 SKU 為 (model, leather, color)"""
    m = SKU_PARTS.match(sku.upper())
    return m.groups() if m else None


def discover_nearby_models(known: set[str], scan_range: int = 30) -> set[str]:
    """
    模型發現掃描：對每個已知 model number，掃描附近 ±scan_range 範圍
    用最常見的皮料+顏色（CK18）探測，找到新 model 後再展開所有顏色
    """
    parsed = [(s, parse_sku(s)) for s in known]
    parsed = [(s, p) for s, p in parsed if p]
    known_models = {int(p[0]) for _, p in parsed}
    log(f"  已知模型數: {len(known_models)}")

    # 用常見 probe 組合（大象灰 CK18 + 黑色 CK89）快速探測新 model
    probe_combos = [("CK", "18"), ("CK", "89"), ("CC", "18")]
    candidate_models: set[int] = set()
    for m in known_models:
        for offset in range(-scan_range, scan_range + 1):
            candidate_models.add(m + offset)
    candidate_models -= known_models

    # 生成探測 SKU
    probe_skus = []
    for model_num in candidate_models:
        model = f"{model_num:06d}"
        for leather, color in probe_combos:
            probe_skus.append(f"{model}{leather}{color}")

    log(f"  模型發現掃描: {len(candidate_models)} 個候選模型 × {len(probe_combos)} 組合 = {len(probe_skus)} SKU")
    return set(probe_skus)


def generate_candidates(known: set[str], min_color_freq: int = 3) -> set[str]:
    """
    生成候選 SKU
    保守策略：對每個模型，擴展同皮料下「常見顏色」
    """
    parsed = [(s, parse_sku(s)) for s in known]
    parsed = [(s, p) for s, p in parsed if p]
    log(f"  解析有效 SKU: {len(parsed)}/{len(known)}")

    # 統計每個顏色的全域出現次數
    color_global_count = Counter(p[2] for _, p in parsed)
    common_colors = {c for c, n in color_global_count.items() if n >= min_color_freq}
    log(f"  全域常見顏色 (≥{min_color_freq} 次): {len(common_colors)}")

    # 模型 -> 皮料 -> 已用顏色
    model_leather_colors: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for _, (model, leather, color) in parsed:
        model_leather_colors[model][leather].add(color)

    candidates: set[str] = set()
    for model, leathers in model_leather_colors.items():
        for leather, used_colors in leathers.items():
            # 預測：該模型 + 同皮料 + 常見顏色但還沒用過
            unseen_colors = common_colors - used_colors
            for color in unseen_colors:
                candidates.add(f"{model}{leather}{color}")

    # 排除已知
    candidates -= {s.upper() for s in known}
    log(f"  生成候選: {len(candidates)}")
    return candidates


def probe_sku(sku: str, timeout: int = 6) -> tuple[str, bool]:
    """探測單一 SKU"""
    try:
        r = requests.head(CDN_PROBE_URL.format(sku=sku), timeout=timeout, allow_redirects=False)
        return sku, r.status_code == 200
    except Exception:
        return sku, False


def probe_batch(skus: list[str], workers: int = 20) -> list[str]:
    """並行探測，回傳存在的 SKU 清單"""
    found = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(probe_sku, s): s for s in skus}
        for i, fut in enumerate(as_completed(futures), 1):
            sku, exists = fut.result()
            if exists:
                found.append(sku)
            if i % 200 == 0:
                log(f"  探測進度: {i}/{len(skus)} (找到 {len(found)})")
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max", type=int, default=5000, help="候選數量上限")
    parser.add_argument("--min-color-freq", type=int, default=3,
                        help="顏色至少出現 N 次才用來擴展")
    args = parser.parse_args()

    log(f"🔮 SKU 預測啟動")
    known = load_known_skus()
    log(f"📚 已知 SKU: {len(known)}")

    # Phase 1: 模型發現 — 掃描已知 model 附近的未知 model
    log(f"🔎 Phase 1: 模型發現掃描")
    discovery_candidates = discover_nearby_models(known, scan_range=30)
    if discovery_candidates and not args.dry_run:
        log(f"🌐 探測 {len(discovery_candidates)} 個模型發現候選...")
        discovery_found = probe_batch(sorted(discovery_candidates), workers=25)
        if discovery_found:
            log(f"🆕 發現 {len(discovery_found)} 個新模型 SKU！")
            # 把新發現的 model 加入 known，後面 Phase 2 會展開所有顏色
            known |= set(discovery_found)
            for sku in sorted(discovery_found):
                log(f"  🆕 {sku}")
        else:
            log(f"  沒有發現新模型")

    # Phase 2: 顏色擴展 — 對所有已知模型展開未見顏色
    log(f"🎨 Phase 2: 顏色擴展")
    candidates = generate_candidates(known, min_color_freq=args.min_color_freq)
    if len(candidates) > args.max:
        log(f"⚠️ 候選 {len(candidates)} 超過上限 {args.max}，截斷")
        candidates = set(list(candidates)[:args.max])

    if args.dry_run:
        log(f"🚫 dry-run，不探測")
        return

    log(f"🌐 開始 CDN 探測 {len(candidates)} 個候選...")
    t0 = time.time()
    found = probe_batch(sorted(candidates), workers=20)
    elapsed = time.time() - t0
    log(f"✅ 探測完成 {elapsed:.1f}s，找到 {len(found)} 個存在的 SKU")

    # 保存預測結果
    payload = {
        "predicted_at": datetime.now().isoformat(),
        "candidates_count": len(candidates),
        "found_count": len(found),
        "found": sorted(found),
        "elapsed_seconds": round(elapsed, 1),
    }
    PREDICTOR_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    log(f"📁 寫到 {PREDICTOR_FILE}")

    if not found:
        log("沒找到新 SKU")
        return

    # Merge into watchlist + 自動標記為 notified（避免被當新品爆 LINE）
    existing_watchlist = []
    if WATCHLIST_FILE.exists():
        try:
            existing_watchlist = json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    new_skus = set(found) - set(existing_watchlist)
    merged = sorted(set(existing_watchlist) | set(found))
    WATCHLIST_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log(f"✅ watchlist: {len(existing_watchlist)} → {len(merged)} (+{len(new_skus)})")

    if new_skus:
        # Read-modify-write 重試 3 次（防 launchd race condition）
        for attempt in range(3):
            state = {}
            if STATE_FILE.exists():
                try:
                    state = json.loads(STATE_FILE.read_text())
                except Exception:
                    pass
            notified = set(state.get("notified", []))
            before = len(notified)
            notified |= new_skus
            state["notified"] = sorted(notified)
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            # 立刻 reload 確認寫成功（沒被 launchd 覆蓋）
            verify = json.loads(STATE_FILE.read_text())
            if new_skus <= set(verify.get("notified", [])):
                log(f"🔇 自動靜音預測 SKU: {before} → {len(notified)} (attempt {attempt+1})")
                break
            time.sleep(0.5)
        else:
            log(f"❌ 寫入 cdn_state.json 連續失敗 3 次（race condition）")


if __name__ == "__main__":
    main()
