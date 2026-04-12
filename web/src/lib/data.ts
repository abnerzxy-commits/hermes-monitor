/**
 * Data layer — 透過 GitHub API 讀寫 repo 裡的 data/*.json
 * 適用 Vercel 部署（filesystem 唯讀）
 */

const GITHUB_OWNER = "abnerzxy-commits";
const GITHUB_REPO = "hermes-monitor";
const GITHUB_BRANCH = "main";
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || "";

const RAW_BASE = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/data`;
const API_BASE = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/data`;

// ─── Types ─────────────────────────────────────────

export interface Product {
  id: string;
  name: string;
  url: string;
  image: string;
  price: string;
  first_seen: string;
}

export interface Subscriber {
  id: string;
  name: string;
  lineUserId: string;
  subscribedProducts: string[];
  createdAt: string;
}

export interface MonitorSource {
  id: string;
  name: string;
  type: "hermes" | "blueberry" | "custom";
  scanInterval: number;
  enabled: boolean;
  lastScan: string | null;
  productCount: number;
  subscribers: string[];
}

export interface RestockEntry {
  name: string;
  url: string;
  price: string;
  timestamp: string;
  weekday: string;
  hour: number;
}

// ─── GitHub read/write helpers ─────────────────────

async function readJsonFromGitHub<T>(filename: string, fallback: T): Promise<T> {
  try {
    // Use GitHub API (no cache) when token available, fallback to raw URL
    if (GITHUB_TOKEN) {
      const res = await fetch(`${API_BASE}/${filename}`, {
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github.v3.raw",
        },
        cache: "no-store",
      });
      if (!res.ok) return fallback;
      return await res.json();
    }
    const url = `${RAW_BASE}/${filename}?t=${Date.now()}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

async function writeJsonToGitHub(filename: string, data: unknown, message?: string): Promise<boolean> {
  if (!GITHUB_TOKEN) {
    console.warn("GITHUB_TOKEN not set, skip write");
    return false;
  }
  try {
    // Get current file sha
    const metaRes = await fetch(`${API_BASE}/${filename}`, {
      headers: { Authorization: `Bearer ${GITHUB_TOKEN}` },
    });

    let sha: string | undefined;
    if (metaRes.ok) {
      const meta = await metaRes.json();
      sha = meta.sha;
    }

    // Write file
    const content = Buffer.from(JSON.stringify(data, null, 2), "utf-8").toString("base64");
    const res = await fetch(`${API_BASE}/${filename}`, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${GITHUB_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: message || `update ${filename}`,
        content,
        sha,
        branch: GITHUB_BRANCH,
      }),
    });
    return res.ok;
  } catch (e) {
    console.error(`writeJsonToGitHub error: ${e}`);
    return false;
  }
}

// ─── Subscribers ───────────────────────────────────

const SUBSCRIBERS_FILE = "subscribers.json";

const DEFAULT_SUBSCRIBERS: Subscriber[] = [
  { id: "abner", name: "Abner", lineUserId: "Ubc7cbeaa0c873e42562b57addff63362", subscribedProducts: ["hermes", "hermes-cdn"], createdAt: "2026-04-01T00:00:00Z" },
  { id: "pangwen", name: "胖雯", lineUserId: "U2b8f1576a62e8dd90c1c1a43853d415b", subscribedProducts: ["hermes", "hermes-cdn"], createdAt: "2026-04-09T00:44:01Z" },
  { id: "chidong", name: "池董", lineUserId: "U667f0a680157296f575dee2e44e015e0", subscribedProducts: ["hermes"], createdAt: "2026-04-09T00:46:47Z" },
];

export async function getSubscribers(): Promise<Subscriber[]> {
  const subs = await readJsonFromGitHub<Subscriber[]>(SUBSCRIBERS_FILE, []);
  if (subs.length === 0) {
    await saveSubscribers(DEFAULT_SUBSCRIBERS);
    return DEFAULT_SUBSCRIBERS;
  }
  return subs;
}

export async function saveSubscribers(subs: Subscriber[]): Promise<boolean> {
  return writeJsonToGitHub(SUBSCRIBERS_FILE, subs, "update subscribers");
}

export async function addSubscriber(name: string, lineUserId: string): Promise<Subscriber> {
  const subs = await getSubscribers();
  const sub: Subscriber = {
    id: Date.now().toString(36),
    name,
    lineUserId,
    subscribedProducts: [],
    createdAt: new Date().toISOString(),
  };
  subs.push(sub);
  await saveSubscribers(subs);
  return sub;
}

export async function updateSubscriber(id: string, updates: Partial<Subscriber>): Promise<Subscriber | null> {
  const subs = await getSubscribers();
  const idx = subs.findIndex((s) => s.id === id);
  if (idx === -1) return null;
  subs[idx] = { ...subs[idx], ...updates };
  await saveSubscribers(subs);
  return subs[idx];
}

export async function deleteSubscriber(id: string): Promise<void> {
  const subs = (await getSubscribers()).filter((s) => s.id !== id);
  await saveSubscribers(subs);
  // Also remove from all sources
  const sources = await getMonitorSources();
  let changed = false;
  for (const src of sources) {
    if (src.subscribers.includes(id)) {
      src.subscribers = src.subscribers.filter((sid) => sid !== id);
      changed = true;
    }
  }
  if (changed) await saveMonitorSources(sources);
}

// ─── Monitor Sources ──────────────────────────────

const SOURCES_FILE = "monitor_sources.json";

const DEFAULT_SOURCES: MonitorSource[] = [
  {
    id: "hermes",
    name: "愛馬仕包包",
    type: "hermes",
    scanInterval: 60,
    enabled: true,
    lastScan: null,
    productCount: 0,
    subscribers: ["abner", "pangwen", "chidong"],
  },
  {
    id: "hermes-cdn",
    name: "愛馬仕 CDN 預警",
    type: "hermes",
    scanInterval: 30,
    enabled: true,
    lastScan: null,
    productCount: 0,
    subscribers: ["abner", "pangwen"],
  },
  {
    id: "blueberry",
    name: "山丘藍藍莓",
    type: "blueberry",
    scanInterval: 86400,
    enabled: true,
    lastScan: null,
    productCount: 0,
    subscribers: ["abner"],
  },
];

export async function getMonitorSources(): Promise<MonitorSource[]> {
  const sources = await readJsonFromGitHub<MonitorSource[]>(SOURCES_FILE, []);
  if (sources.length === 0) {
    await saveMonitorSources(DEFAULT_SOURCES);
    return DEFAULT_SOURCES;
  }
  return sources;
}

export async function saveMonitorSources(sources: MonitorSource[]): Promise<boolean> {
  return writeJsonToGitHub(SOURCES_FILE, sources, "update monitor sources");
}

export async function updateMonitorSource(id: string, updates: Partial<MonitorSource>): Promise<MonitorSource | null> {
  const sources = await getMonitorSources();
  const idx = sources.findIndex((s) => s.id === id);
  if (idx === -1) return null;
  sources[idx] = { ...sources[idx], ...updates };
  await saveMonitorSources(sources);
  return sources[idx];
}

// ─── Products (read-only) ─────────────────────────

export async function getHermesProducts(): Promise<Record<string, Product>> {
  return readJsonFromGitHub<Record<string, Product>>("products.json", {});
}

export async function getRestockHistory(): Promise<RestockEntry[]> {
  return readJsonFromGitHub<RestockEntry[]>("restock_history.json", []);
}

export async function getSkuWatchlist(): Promise<string[]> {
  return readJsonFromGitHub<string[]>("sku_watchlist.json", []);
}

export async function getCdnState(): Promise<{ notified: string[]; last_scan: string | null }> {
  return readJsonFromGitHub("cdn_state.json", { notified: [], last_scan: null });
}

// ─── Subscribe/Unsubscribe ────────────────────────

export async function subscribeUserToSource(subscriberId: string, sourceId: string): Promise<boolean> {
  const sources = await getMonitorSources();
  const src = sources.find((s) => s.id === sourceId);
  if (!src) return false;
  if (!src.subscribers.includes(subscriberId)) {
    src.subscribers.push(subscriberId);
    await saveMonitorSources(sources);
  }
  const subs = await getSubscribers();
  const sub = subs.find((s) => s.id === subscriberId);
  if (sub && !sub.subscribedProducts.includes(sourceId)) {
    sub.subscribedProducts.push(sourceId);
    await saveSubscribers(subs);
  }
  return true;
}

export async function unsubscribeUserFromSource(subscriberId: string, sourceId: string): Promise<boolean> {
  const sources = await getMonitorSources();
  const src = sources.find((s) => s.id === sourceId);
  if (src) {
    src.subscribers = src.subscribers.filter((id) => id !== subscriberId);
    await saveMonitorSources(sources);
  }
  const subs = await getSubscribers();
  const sub = subs.find((s) => s.id === subscriberId);
  if (sub) {
    sub.subscribedProducts = sub.subscribedProducts.filter((id) => id !== sourceId);
    await saveSubscribers(subs);
  }
  return true;
}
