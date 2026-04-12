import { NextRequest } from "next/server";

const LINE_TOKEN = process.env.LINE_CHANNEL_ACCESS_TOKEN || "";
const LINE_USER_ID = process.env.LINE_USER_ID || "";
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || "";
const CRON_SECRET = process.env.CRON_SECRET || "";

const GITHUB_OWNER = "abnerzxy-commits";
const GITHUB_REPO = "hermes-monitor";
const GITHUB_BRANCH = "main";
const API_BASE = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/data`;

const CATEGORY_URLS = [
  "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/",
  "https://www.hermes.com/tw/zh/category/leather-goods/bags-and-clutches/womens-bags-and-clutches/",
  "https://www.hermes.com/tw/zh/category/leather-goods/small-leather-goods/",
];

const USER_AGENTS = [
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
];

interface Product {
  id: string;
  name: string;
  url: string;
  image: string;
  price: string;
  sku: string;
  first_seen: string;
}

// ─── GitHub helpers ──────────────────

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

async function writeGitHub(filename: string, data: unknown, message: string): Promise<boolean> {
  if (!GITHUB_TOKEN) return false;
  try {
    const metaRes = await fetch(`${API_BASE}/${filename}`, {
      headers: { Authorization: `Bearer ${GITHUB_TOKEN}` },
    });
    let sha: string | undefined;
    if (metaRes.ok) {
      const meta = await metaRes.json();
      sha = meta.sha;
    }
    const content = Buffer.from(JSON.stringify(data, null, 2), "utf-8").toString("base64");
    const res = await fetch(`${API_BASE}/${filename}`, {
      method: "PUT",
      headers: { Authorization: `Bearer ${GITHUB_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ message, content, sha, branch: GITHUB_BRANCH }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// ─── Scraper ──────────────────────────

function extractProducts(html: string): Product[] {
  // Find hermes-state JSON
  const stateMatch = html.match(/<script\s+id=["']?hermes-state["']?\s+type="application\/json">([\s\S]*?)<\/script>/);
  if (!stateMatch) return [];

  let state: Record<string, unknown>;
  try {
    state = JSON.parse(stateMatch[1]);
  } catch {
    return [];
  }

  const products: Product[] = [];
  for (const [, entry] of Object.entries(state)) {
    if (typeof entry !== "object" || !entry) continue;
    const body = (entry as Record<string, unknown>).b;
    if (typeof body !== "object" || !body) continue;
    const prodData = (body as Record<string, unknown>).products;
    if (typeof prodData !== "object" || !prodData) continue;
    const items = (prodData as Record<string, unknown>).items;
    if (!Array.isArray(items)) continue;

    for (const item of items) {
      const sku = item.sku || "";
      const title = item.title || "";
      const price = item.price || 0;
      const urlPath = item.url || "";
      const assets = item.assets || [];

      let image = "";
      for (const asset of assets) {
        const imgUrl = asset.url || "";
        if (imgUrl) {
          image = imgUrl.startsWith("//") ? `https:${imgUrl}` : imgUrl;
          break;
        }
      }

      const fullUrl = urlPath.startsWith("http")
        ? urlPath
        : `https://www.hermes.com${urlPath}`;

      const id = Buffer.from(sku || fullUrl).toString("hex").slice(0, 12);

      products.push({
        id,
        name: title,
        url: fullUrl,
        image,
        price: typeof price === "number" ? `NT$ ${price.toLocaleString()}` : String(price),
        sku,
        first_seen: new Date().toISOString(),
      });
    }
  }
  return products;
}

async function scrapeCategory(url: string): Promise<Product[]> {
  const ua = USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent": ua,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Accept-Encoding": "identity",
      },
    });
    if (!res.ok) return [];
    const html = await res.text();
    if (html.length < 200000) return []; // DataDome fake page
    return extractProducts(html);
  } catch {
    return [];
  }
}

// ─── LINE notification ─────────────────

async function sendLineNotification(newProducts: Product[]) {
  if (!LINE_TOKEN || !LINE_USER_ID || newProducts.length === 0) return;

  // Send to all subscribers
  const subscribers = await readGitHub<Array<{ lineUserId: string; subscribedProducts: string[] }>>("subscribers.json", []);
  const hermesSubscribers = subscribers.filter((s) => s.subscribedProducts.includes("hermes"));
  const userIds = hermesSubscribers.map((s) => s.lineUserId);
  if (userIds.length === 0) userIds.push(LINE_USER_ID); // fallback to admin

  const lines = newProducts.slice(0, 10).map((p) => `${p.name}\n${p.price}\n${p.url}`);
  const text = `🛍️ 愛馬仕新品上架！(${newProducts.length} 件)\n\n${lines.join("\n\n")}`;

  for (const uid of userIds) {
    await fetch("https://api.line.me/v2/bot/message/push", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${LINE_TOKEN}`,
      },
      body: JSON.stringify({
        to: uid,
        messages: [{ type: "text", text }],
      }),
    });
  }
}

// ─── Main handler ──────────────────────

export async function GET(req: NextRequest) {
  // Verify cron secret
  const authHeader = req.headers.get("authorization");
  if (CRON_SECRET && authHeader !== `Bearer ${CRON_SECRET}`) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const startTime = Date.now();

  // Scrape all categories
  const allProducts: Product[] = [];
  const seenSkus = new Set<string>();

  for (const url of CATEGORY_URLS) {
    const products = await scrapeCategory(url);
    for (const p of products) {
      if (!seenSkus.has(p.sku)) {
        seenSkus.add(p.sku);
        allProducts.push(p);
      }
    }
    // Small delay between requests
    await new Promise((r) => setTimeout(r, 1000 + Math.random() * 2000));
  }

  if (allProducts.length === 0) {
    return Response.json({
      status: "no_data",
      message: "All requests blocked or empty",
      duration: Date.now() - startTime,
    });
  }

  // Load existing products
  const existing = await readGitHub<Record<string, Product>>("products.json", {});
  const existingSkus = new Set(Object.values(existing).map((p) => p.sku));

  // Find new products
  const newProducts: Product[] = [];
  const updatedProducts: Record<string, Product> = { ...existing };

  for (const p of allProducts) {
    if (!existingSkus.has(p.sku)) {
      newProducts.push(p);
      updatedProducts[p.id] = p;
    }
  }

  // Save if there are new products
  if (newProducts.length > 0) {
    await writeGitHub("products.json", updatedProducts, `new products: ${newProducts.map((p) => p.name).join(", ")}`);

    // Update restock history
    const history = await readGitHub<Array<Record<string, unknown>>>("restock_history.json", []);
    for (const p of newProducts) {
      history.push({
        name: p.name,
        url: p.url,
        price: p.price,
        sku: p.sku,
        timestamp: new Date().toISOString(),
      });
    }
    await writeGitHub("restock_history.json", history, `restock: ${newProducts.length} new`);

    // Send LINE notifications
    await sendLineNotification(newProducts);
  }

  return Response.json({
    status: "ok",
    totalScraped: allProducts.length,
    newProducts: newProducts.length,
    newItems: newProducts.map((p) => ({ name: p.name, price: p.price, sku: p.sku })),
    duration: Date.now() - startTime,
  });
}
