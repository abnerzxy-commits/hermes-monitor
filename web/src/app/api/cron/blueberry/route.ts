import { NextRequest } from "next/server";

const LINE_TOKEN = process.env.LINE_CHANNEL_ACCESS_TOKEN || "";
const CRON_SECRET = process.env.CRON_SECRET || "";
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || "";

const GITHUB_OWNER = "abnerzxy-commits";
const GITHUB_REPO = "hermes-monitor";
const GITHUB_BRANCH = "main";
const API_BASE = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/data`;

const BLUEBERRY_API = "https://api.taiwanblueberry.com/api/frontend/products/productDetails";
const PRODUCT_JSON = "https://api.taiwanblueberry.com/public/json/product.json";

// SKU pattern decoder: BBL25{size}{qty}{box}
// size: 13=中果, 15=大果, 17=?, 18=特大果, 21=超大果
// qty: 04=4入, 05=5入, 08=8入
// box: 01=一箱, 04=四箱
const SIZE_MAP: Record<string, string> = { "13": "中果", "15": "大果", "17": "中大果", "18": "特大果", "21": "超大果" };
const QTY_MAP: Record<string, string> = { "04": "4入", "05": "5入", "08": "8入" };

function decodeSku(sku: string): string | null {
  const m = sku.match(/^BBL25(\d{2})(\d{2})(\d{2})$/);
  if (!m) return null;
  const size = SIZE_MAP[m[1]];
  const qty = QTY_MAP[m[2]];
  if (!size || !qty) return null;
  const box = m[3] === "04" ? "/四箱" : "";
  return `${size}${qty}${box}`;
}

// ─── Types ───────────────────────────

interface BlueberryAvail {
  sku: string;
  ps_id: number;
  available: number | null;
  remark: string;
  waiting_days: number | null;
}

interface ProductInfo {
  name: string;
  size: string;
  price: number;
  weight: string;
}

interface Subscriber {
  lineUserId: string;
  subscribedProducts: string[];
}

// ─── Helpers ─────────────────────────

async function readGitHub<T>(filename: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}/${filename}`, {
      headers: { Authorization: `Bearer ${GITHUB_TOKEN}`, Accept: "application/vnd.github.v3.raw" },
      cache: "no-store",
    });
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

async function sendLinePush(userId: string, text: string) {
  if (!LINE_TOKEN) return;
  await fetch("https://api.line.me/v2/bot/message/push", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${LINE_TOKEN}`,
    },
    body: JSON.stringify({
      to: userId,
      messages: [{ type: "text", text }],
    }),
  });
}

// Strip UTF-8 BOM if present
function stripBom(text: string): string {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

// ─── Main ────────────────────────────

export async function GET(req: NextRequest) {
  const authHeader = req.headers.get("authorization");
  if (CRON_SECRET && authHeader !== `Bearer ${CRON_SECRET}`) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    // 1. Fetch product names from product.json
    const namesRes = await fetch(PRODUCT_JSON, {
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    const namesText = stripBom(await namesRes.text());
    const namesData = JSON.parse(namesText);

    // Build ps_id -> product info map
    const psMap = new Map<number, ProductInfo>();
    for (const product of namesData.product || []) {
      for (const w of product.weight || []) {
        const psId = Number(w.ps_id);
        if (psId > 0) {
          psMap.set(psId, {
            name: product.name || "",
            size: w.number || "",
            price: Number(w.price) || 0,
            weight: w.weight || "",
          });
        }
      }
    }

    // 2. Fetch availability
    const availRes = await fetch(BLUEBERRY_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const availText = stripBom(await availRes.text());
    const availData = JSON.parse(availText);
    const items: BlueberryAvail[] = availData.data || [];

    // 3. Filter to current-season products (have waiting_days)
    const activeProducts: Array<{
      name: string;
      price: number;
      size: string;
      available: number;
      remark: string;
      sku: string;
      inStock: boolean;
    }> = [];

    for (const item of items) {
      const psId = Number(item.ps_id);
      const avail = Number(item.available ?? -99);
      const waiting = item.waiting_days ? Number(item.waiting_days) : null;

      // Only current season: has ps_id > 0 and waiting_days > 0
      if (psId <= 0 || !waiting || waiting <= 0) continue;

      const info = psMap.get(psId);
      activeProducts.push({
        name: info?.name || decodeSku(item.sku) || item.sku,
        price: info?.price || 0,
        size: info?.size || "",
        available: avail,
        remark: item.remark || "",
        sku: item.sku,
        inStock: avail > 0,
      });
    }

    // 4. Build notification message
    const now = new Date();
    const twTime = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const dateStr = `${twTime.getMonth() + 1}/${twTime.getDate()}`;

    const inStock = activeProducts.filter((p) => p.inStock);
    const outOfStock = activeProducts.filter((p) => !p.inStock && !p.remark.includes("停售"));
    const discontinued = activeProducts.filter((p) => p.remark.includes("停售"));

    let msg = `🫐 山丘藍藍莓 ${dateStr} 庫存日報\n`;

    if (inStock.length > 0) {
      msg += `\n✅ 有貨 (${inStock.length} 項)：\n`;
      for (const p of inStock) {
        msg += `• ${p.name}`;
        if (p.price) msg += ` $${p.price.toLocaleString()}`;
        if (p.size) msg += ` (${p.size})`;
        msg += ` — 剩 ${p.available} 組\n`;
      }
    } else {
      msg += `\n❌ 目前全部缺貨\n`;
    }

    if (outOfStock.length > 0) {
      msg += `\n⏳ 等待補貨 (${outOfStock.length} 項)：\n`;
      for (const p of outOfStock) {
        msg += `• ${p.name}`;
        if (p.price) msg += ` $${p.price.toLocaleString()}`;
        if (p.remark) msg += `\n  ${p.remark}`;
        msg += `\n`;
      }
    }

    if (discontinued.length > 0) {
      msg += `\n🚫 產季末停售 (${discontinued.length} 項)：\n`;
      for (const p of discontinued) {
        msg += `• ${p.name}`;
        if (p.price) msg += ` $${p.price.toLocaleString()}`;
        msg += `\n`;
      }
    }

    msg += `\n🔗 taiwanblueberry.com/store`;

    // 5. Send to blueberry subscribers
    const subscribers = await readGitHub<Subscriber[]>("subscribers.json", []);
    const targets = subscribers.filter((s) => s.subscribedProducts.includes("blueberry"));

    let sent = 0;
    for (const sub of targets) {
      await sendLinePush(sub.lineUserId, msg);
      sent++;
    }

    return Response.json({
      status: "ok",
      activeProducts: activeProducts.length,
      inStock: inStock.length,
      outOfStock: outOfStock.length,
      discontinued: discontinued.length,
      notifiedUsers: sent,
      message: msg,
    });
  } catch (err) {
    return Response.json(
      { error: "Failed", detail: String(err) },
      { status: 500 }
    );
  }
}
