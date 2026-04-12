import { NextRequest } from "next/server";
import {
  getSubscribers,
  addSubscriber,
  updateSubscriber,
  deleteSubscriber,
  getMonitorSources,
  updateMonitorSource,
  deleteMonitorSource,
  getHermesProducts,
  getRestockHistory,
  getCdnState,
  getSkuWatchlist,
  subscribeUserToSource,
  unsubscribeUserFromSource,
} from "@/lib/data";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "hermes2026";

function checkAuth(req: NextRequest): boolean {
  const auth = req.headers.get("authorization");
  if (!auth) return false;
  const token = auth.replace("Bearer ", "");
  return token === ADMIN_PASSWORD;
}

export async function GET(req: NextRequest) {
  if (!checkAuth(req)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const [subscribers, sources, products, history, cdnState, watchlist] = await Promise.all([
    getSubscribers(),
    getMonitorSources(),
    getHermesProducts(),
    getRestockHistory(),
    getCdnState(),
    getSkuWatchlist(),
  ]);

  return Response.json({
    subscribers,
    sources,
    products: Object.values(products),
    stats: {
      totalSubscribers: subscribers.length,
      totalSources: sources.length,
      totalProducts: Object.keys(products).length,
      totalRestocks: history.length,
      cdnTracking: watchlist.length,
      cdnNotified: cdnState.notified.length,
      lastCdnScan: cdnState.last_scan,
    },
    recentRestocks: history.slice(-20).reverse(),
  });
}

export async function POST(req: NextRequest) {
  if (!checkAuth(req)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await req.json();
  const { action } = body;

  switch (action) {
    case "add_subscriber": {
      const { name, lineUserId } = body;
      if (!name || !lineUserId) {
        return Response.json({ error: "name 和 lineUserId 必填" }, { status: 400 });
      }
      const sub = await addSubscriber(name, lineUserId);
      return Response.json({ success: true, subscriber: sub });
    }

    case "update_subscriber": {
      const { id, ...updates } = body;
      if (!id) return Response.json({ error: "id 必填" }, { status: 400 });
      const result = await updateSubscriber(id, updates);
      if (!result) return Response.json({ error: "找不到用戶" }, { status: 404 });
      return Response.json({ success: true, subscriber: result });
    }

    case "delete_subscriber": {
      const { id } = body;
      if (!id) return Response.json({ error: "id 必填" }, { status: 400 });
      await deleteSubscriber(id);
      return Response.json({ success: true });
    }

    case "update_source": {
      const { id, ...updates } = body;
      if (!id) return Response.json({ error: "id 必填" }, { status: 400 });
      const result = await updateMonitorSource(id, updates);
      if (!result) return Response.json({ error: "找不到監控源" }, { status: 404 });
      return Response.json({ success: true, source: result });
    }

    case "subscribe": {
      const { subscriberId, sourceId } = body;
      await subscribeUserToSource(subscriberId, sourceId);
      return Response.json({ success: true });
    }

    case "unsubscribe": {
      const { subscriberId, sourceId } = body;
      await unsubscribeUserFromSource(subscriberId, sourceId);
      return Response.json({ success: true });
    }

    case "delete_source": {
      const { id } = body;
      if (!id) return Response.json({ error: "id 必填" }, { status: 400 });
      await deleteMonitorSource(id);
      return Response.json({ success: true });
    }

    default:
      return Response.json({ error: `未知操作: ${action}` }, { status: 400 });
  }
}
