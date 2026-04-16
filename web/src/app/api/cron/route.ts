import { NextRequest } from "next/server";
import { request as undiciRequest, ProxyAgent, Agent } from "undici";

export const runtime = "nodejs";
export const maxDuration = 60;

const LINE_TOKEN = process.env.LINE_CHANNEL_ACCESS_TOKEN || "";
const LINE_USER_ID = process.env.LINE_USER_ID || "";
const LINE_PAUSE_UNTIL = process.env.LINE_PAUSE_UNTIL || "";
const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const TG_CHAT_ID = process.env.TELEGRAM_CHAT_ID || "";
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || "";
const CRON_SECRET = process.env.CRON_SECRET || "";
const PROXY_URL = process.env.CAPTCHA_PROXY || "";

const scrapeDispatcher = PROXY_URL ? new ProxyAgent(PROXY_URL) : new Agent();

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

const DEDUPE_WINDOW_MS = 6 * 60 * 60 * 1000;
const NOTIFIED_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

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

async function scrapeCategory(url: string): Promise<{ products: Product[]; status: string }> {
  const ua = USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
  try {
    const { statusCode, body } = await undiciRequest(url, {
      method: "GET",
      headers: {
        "User-Agent": ua,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Accept-Encoding": "identity",
      },
      dispatcher: scrapeDispatcher,
      bodyTimeout: 25_000,
      headersTimeout: 15_000,
    });
    if (statusCode !== 200) {
      body.dump().catch(() => {});
      return { products: [], status: `http_${statusCode}` };
    }
    const html = await body.text();
    if (html.length < 200000) return { products: [], status: "blocked" };
    return { products: extractProducts(html), status: "ok" };
  } catch (e) {
    return { products: [], status: `error:${(e as Error).message.slice(0, 60)}` };
  }
}

// ─── Notification channels ─────────────

function isLinePaused(): boolean {
  if (!LINE_PAUSE_UNTIL) return false;
  const until = Date.parse(LINE_PAUSE_UNTIL);
  return Number.isFinite(until) && Date.now() < until;
}

function buildMessage(products: Product[]): string {
  const lines = products.slice(0, 10).map((p) => `🆕 ${p.name}\n💰 ${p.price}\n🔗 ${p.url}`);
  const more = products.length > 10 ? `\n\n…還有 ${products.length - 10} 件` : "";
  return `🛍️ 愛馬仕新品上架！(${products.length} 件)\n\n${lines.join("\n\n")}${more}`;
}

async function sendTelegram(text: string): Promise<boolean> {
  if (!TG_TOKEN || !TG_CHAT_ID) return false;
  try {
    const res = await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: TG_CHAT_ID,
        text,
        disable_web_page_preview: true,
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function sendLine(text: string): Promise<{ sent: number; status: string }> {
  if (isLinePaused()) return { sent: 0, status: `paused_until_${LINE_PAUSE_UNTIL}` };
  if (!LINE_TOKEN || !LINE_USER_ID) return { sent: 0, status: "no_credentials" };

  const subscribers = await readGitHub<Array<{ lineUserId: string; subscribedProducts: string[] }>>("subscribers.json", []);
  const userIds = subscribers.filter((s) => s.subscribedProducts.includes("hermes")).map((s) => s.lineUserId);
  if (userIds.length === 0) userIds.push(LINE_USER_ID);

  let sent = 0;
  let lastErr = "";
  for (const uid of userIds) {
    try {
      const res = await fetch("https://api.line.me/v2/bot/message/push", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${LINE_TOKEN}` },
        body: JSON.stringify({ to: uid, messages: [{ type: "text", text }] }),
      });
      if (res.ok) sent++;
      else lastErr = `http_${res.status}`;
    } catch (e) {
      lastErr = (e as Error).message.slice(0, 40);
    }
  }
  return { sent, status: sent > 0 ? "ok" : lastErr || "none" };
}

// ─── Main handler ──────────────────────

export async function GET(req: NextRequest) {
  const authHeader = req.headers.get("authorization");
  if (CRON_SECRET && authHeader !== `Bearer ${CRON_SECRET}`) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const startTime = Date.now();

  const scrapePromise = Promise.all(CATEGORY_URLS.map((u) => scrapeCategory(u)));
  const existingPromise = readGitHub<Record<string, Product>>("products.json", {});
  const notifiedPromise = readGitHub<Record<string, string>>("notified_skus.json", {});
  const historyPromise = readGitHub<Array<Record<string, unknown>>>("restock_history.json", []);

  const scrapeResults = await scrapePromise;
  const allProducts: Product[] = [];
  const seenSkus = new Set<string>();
  const categoryStatus: Array<{ url: string; status: string; count: number }> = [];
  CATEGORY_URLS.forEach((url, i) => {
    const { products, status } = scrapeResults[i];
    categoryStatus.push({ url, status, count: products.length });
    for (const p of products) {
      if (!seenSkus.has(p.sku)) {
        seenSkus.add(p.sku);
        allProducts.push(p);
      }
    }
  });

  if (allProducts.length === 0) {
    return Response.json({
      status: "no_data",
      message: "All requests blocked or empty",
      proxy: PROXY_URL ? "on" : "off",
      categoryStatus,
      duration: Date.now() - startTime,
    });
  }

  const existing = await existingPromise;
  const existingSkus = new Set(Object.values(existing).map((p) => p.sku));

  const newProducts: Product[] = allProducts.filter((p) => !existingSkus.has(p.sku));

  let notifySent = { telegram: false, line: { sent: 0, status: "skipped" as string } };
  let productsToNotify: Product[] = [];
  let suppressedCount = 0;

  if (newProducts.length > 0) {
    const notified = await notifiedPromise;
    const now = Date.now();

    productsToNotify = newProducts.filter((p) => {
      const last = notified[p.sku];
      if (!last) return true;
      const age = now - Date.parse(last);
      return !Number.isFinite(age) || age > DEDUPE_WINDOW_MS;
    });
    suppressedCount = newProducts.length - productsToNotify.length;

    const updatedProducts: Record<string, Product> = { ...existing };
    for (const p of newProducts) updatedProducts[p.id] = p;

    const writes: Array<Promise<unknown>> = [
      writeGitHub(
        "products.json",
        updatedProducts,
        `scan: +${newProducts.length} skus`
      ),
    ];

    if (productsToNotify.length > 0) {
      const text = buildMessage(productsToNotify);
      const nowIso = new Date().toISOString();

      const [tg, line, history] = await Promise.all([
        sendTelegram(text),
        sendLine(text),
        historyPromise,
      ]);
      notifySent = { telegram: tg, line };

      for (const p of productsToNotify) {
        history.push({
          name: p.name,
          url: p.url,
          price: p.price,
          sku: p.sku,
          timestamp: nowIso,
        });
        notified[p.sku] = nowIso;
      }
      for (const sku of Object.keys(notified)) {
        const age = now - Date.parse(notified[sku]);
        if (Number.isFinite(age) && age > NOTIFIED_RETENTION_MS) delete notified[sku];
      }

      writes.push(
        writeGitHub(
          "restock_history.json",
          history,
          `restock: ${productsToNotify.length} new`
        ),
        writeGitHub(
          "notified_skus.json",
          notified,
          `notified: ${productsToNotify.length} sku(s)`
        )
      );
    }

    await Promise.all(writes);
  }

  return Response.json({
    status: "ok",
    totalScraped: allProducts.length,
    newProducts: newProducts.length,
    notified: productsToNotify.length,
    suppressedByDedupe: suppressedCount,
    notifySent,
    newItems: newProducts.map((p) => ({ name: p.name, price: p.price, sku: p.sku })),
    proxy: PROXY_URL ? "on" : "off",
    linePaused: isLinePaused() ? LINE_PAUSE_UNTIL : null,
    categoryStatus,
    duration: Date.now() - startTime,
  });
}
