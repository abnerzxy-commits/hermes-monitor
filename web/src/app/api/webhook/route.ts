import { NextRequest } from "next/server";
import { getSubscribers, addSubscriber } from "@/lib/data";

const LINE_CHANNEL_ACCESS_TOKEN = process.env.LINE_CHANNEL_ACCESS_TOKEN || "";

async function getLineProfile(userId: string): Promise<{ displayName: string } | null> {
  try {
    const res = await fetch(`https://api.line.me/v2/bot/profile/${userId}`, {
      headers: { Authorization: `Bearer ${LINE_CHANNEL_ACCESS_TOKEN}` },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function replyMessage(replyToken: string, text: string) {
  if (!LINE_CHANNEL_ACCESS_TOKEN) return;
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${LINE_CHANNEL_ACCESS_TOKEN}`,
    },
    body: JSON.stringify({
      replyToken,
      messages: [{ type: "text", text }],
    }),
  });
}

// LINE webhook verification
export async function GET() {
  return Response.json({ status: "ok" });
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const events = body.events || [];

  for (const event of events) {
    const userId = event.source?.userId;
    if (!userId) continue;

    if (event.type === "follow" || event.type === "message") {
      // Check if user already exists
      const subs = await getSubscribers();
      const exists = subs.find((s) => s.lineUserId === userId);

      if (!exists) {
        // Get LINE display name
        const profile = await getLineProfile(userId);
        const name = profile?.displayName || `User_${userId.slice(-6)}`;

        // Auto-add as subscriber
        await addSubscriber(name, userId);

        if (event.replyToken) {
          await replyMessage(
            event.replyToken,
            `${name} 你好！已加入盯貨通知名單 📡\n\n你會收到以下通知：\n• 愛馬仕新品上架\n• CDN 預警偵測\n\n管理員會幫你設定訂閱項目。`
          );
        }
      } else if (event.type === "message" && event.replyToken) {
        await replyMessage(
          event.replyToken,
          `${exists.name}，你已在通知名單中 ✅\n目前訂閱：${exists.subscribedProducts.length} 項監控`
        );
      }
    }

    if (event.type === "unfollow") {
      // Don't auto-delete, just log
      console.log(`User ${userId} unfollowed`);
    }
  }

  return Response.json({ status: "ok" });
}
