"use client";

import { useState, useEffect, useCallback } from "react";

/* ═══════════════ Types ═══════════════ */

interface Subscriber {
  id: string;
  name: string;
  lineUserId: string;
  subscribedProducts: string[];
  createdAt: string;
}

interface MonitorSource {
  id: string;
  name: string;
  type: "hermes" | "blueberry" | "custom";
  scanInterval: number;
  enabled: boolean;
  lastScan: string | null;
  productCount: number;
  subscribers: string[];
}

interface Product {
  id: string;
  name: string;
  url: string;
  image: string;
  price: string;
  first_seen: string;
}

interface RestockEntry {
  name: string;
  url: string;
  price: string;
  timestamp: string;
}

interface Stats {
  totalSubscribers: number;
  totalSources: number;
  totalProducts: number;
  totalRestocks: number;
  cdnTracking: number;
  cdnNotified: number;
  lastCdnScan: string | null;
}

interface AdminData {
  subscribers: Subscriber[];
  sources: MonitorSource[];
  products: Product[];
  stats: Stats;
  recentRestocks: RestockEntry[];
}

interface ToastState {
  message: string;
  type: "success" | "error";
}

/* ═══════════════ Styles ═══════════════ */

const C = {
  bg: "#F5F1EB",
  card: "#FFFFFF",
  border: "#E8E0D8",
  primary: "#C47530",
  primaryLight: "#E8A860",
  accent: "#8B6914",
  success: "#7FA07C",
  warning: "#C4975A",
  danger: "#B87070",
  text: "#3A3A3A",
  muted: "#8A8A8A",
  subtle: "#C8C0B8",
  hermes: "#F37021",
  blueberry: "#4A6FA5",
};

/* ═══════════════ Helpers ═══════════════ */

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分鐘`;
  return `${Math.round(seconds / 3600)} 小時`;
}

function timeAgo(isoStr: string | null): string {
  if (!isoStr) return "從未";
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "剛剛";
  if (mins < 60) return `${mins} 分鐘前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小時前`;
  return `${Math.floor(hrs / 24)} 天前`;
}

function sourceIcon(type: string): string {
  if (type === "hermes") return "🧡";
  if (type === "blueberry") return "🫐";
  return "📦";
}

/* ═══════════════ Component ═══════════════ */

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [data, setData] = useState<AdminData | null>(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  // Left panel
  const [selectedSubId, setSelectedSubId] = useState<string | null>(null);
  const [subSearch, setSubSearch] = useState("");
  const [showAddSub, setShowAddSub] = useState(false);
  const [addSubForm, setAddSubForm] = useState({ name: "", lineUserId: "" });

  // Right panel
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [editingInterval, setEditingInterval] = useState<string | null>(null);
  const [intervalValue, setIntervalValue] = useState("");

  /* --- Init --- */
  useEffect(() => {
    const saved = localStorage.getItem("monitor_admin_token");
    if (saved) setToken(saved);
  }, []);

  /* --- Fetch --- */
  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const res = await fetch("/api/admin", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        localStorage.removeItem("monitor_admin_token");
        setToken("");
        return;
      }
      const json = await res.json();
      setData(json);
      if (!selectedSourceId && json.sources?.length > 0) {
        setSelectedSourceId(json.sources[0].id);
      }
    } catch {
      showToast("載入失敗", "error");
    } finally {
      setLoading(false);
    }
  }, [token, selectedSourceId]);

  useEffect(() => {
    if (token) refresh();
  }, [token, refresh]);

  /* --- Helpers --- */
  function showToast(message: string, type: "success" | "error" = "success") {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }

  async function apiPost(body: Record<string, unknown>) {
    const res = await fetch("/api/admin", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  /* --- Actions --- */
  const handleLogin = () => {
    if (!password.trim()) return;
    localStorage.setItem("monitor_admin_token", password);
    setToken(password);
    setPassword("");
  };

  const handleAddSubscriber = async () => {
    if (!addSubForm.name || !addSubForm.lineUserId) {
      showToast("請填寫完整", "error");
      return;
    }
    const result = await apiPost({ action: "add_subscriber", ...addSubForm });
    if (result.success) {
      showToast("已新增用戶");
      setShowAddSub(false);
      setAddSubForm({ name: "", lineUserId: "" });
      refresh();
    } else {
      showToast(result.error, "error");
    }
  };

  const handleDeleteSubscriber = async (id: string, name: string) => {
    if (!confirm(`確定要刪除 ${name} 嗎？`)) return;
    const result = await apiPost({ action: "delete_subscriber", id });
    if (result.success) {
      showToast("已刪除");
      if (selectedSubId === id) setSelectedSubId(null);
      refresh();
    }
  };

  const handleToggleSubscription = async (subscriberId: string, sourceId: string, subscribed: boolean) => {
    const result = await apiPost({
      action: subscribed ? "unsubscribe" : "subscribe",
      subscriberId,
      sourceId,
    });
    if (result.success) refresh();
  };

  const handleUpdateInterval = async (sourceId: string) => {
    const seconds = parseInt(intervalValue);
    if (isNaN(seconds) || seconds < 10) {
      showToast("間隔至少 10 秒", "error");
      return;
    }
    const result = await apiPost({ action: "update_source", id: sourceId, scanInterval: seconds });
    if (result.success) {
      showToast("已更新掃描頻率");
      setEditingInterval(null);
      refresh();
    }
  };

  const handleToggleSource = async (sourceId: string, enabled: boolean) => {
    const result = await apiPost({ action: "update_source", id: sourceId, enabled: !enabled });
    if (result.success) refresh();
  };

  /* --- Login screen --- */
  if (!token) {
    return (
      <div style={{ background: C.bg, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{
          background: C.card, borderRadius: 20, padding: 40, boxShadow: "0 8px 32px rgba(0,0,0,.08)",
          width: 380, textAlign: "center",
        }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🔒</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, marginBottom: 8 }}>盯貨監控後台</h1>
          <p style={{ color: C.muted, fontSize: 14, marginBottom: 24 }}>Hermès + 山丘藍 統一管理</p>
          <input
            type="password"
            placeholder="管理密碼"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            style={{
              width: "100%", padding: "12px 16px", borderRadius: 10,
              border: `1px solid ${C.border}`, fontSize: 15, marginBottom: 16,
              outline: "none", boxSizing: "border-box",
            }}
          />
          <button
            onClick={handleLogin}
            style={{
              width: "100%", padding: "12px", borderRadius: 10, border: "none",
              background: C.primary, color: "#fff", fontSize: 15, fontWeight: 600,
              cursor: "pointer",
            }}
          >
            登入
          </button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ background: C.bg, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ color: C.muted, fontSize: 16 }}>載入中...</div>
      </div>
    );
  }

  const selectedSub = data.subscribers.find((s) => s.id === selectedSubId);
  const selectedSource = data.sources.find((s) => s.id === selectedSourceId);
  const filteredSubs = data.subscribers.filter(
    (s) => !subSearch || s.name.toLowerCase().includes(subSearch.toLowerCase()) || s.lineUserId.includes(subSearch)
  );

  /* --- Main layout --- */
  return (
    <div style={{ background: C.bg, minHeight: "100vh" }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", top: 20, right: 20, zIndex: 999,
          background: toast.type === "success" ? C.success : C.danger,
          color: "#fff", padding: "12px 20px", borderRadius: 10,
          fontSize: 14, fontWeight: 500, boxShadow: "0 4px 12px rgba(0,0,0,.15)",
          animation: "fadeInUp .3s ease",
        }}>
          {toast.message}
        </div>
      )}

      {/* Header */}
      <div style={{
        background: C.card, borderBottom: `1px solid ${C.border}`,
        padding: "16px 24px", display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 28 }}>📡</span>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: 0 }}>盯貨監控後台</h1>
            <p style={{ fontSize: 12, color: C.muted, margin: 0 }}>
              {data.stats.totalProducts} 商品 · {data.stats.cdnTracking} SKU 追蹤 · {data.stats.totalSubscribers} 訂閱者
            </p>
          </div>
        </div>
        <button
          onClick={() => { localStorage.removeItem("monitor_admin_token"); setToken(""); setData(null); }}
          style={{
            padding: "8px 16px", borderRadius: 8, border: `1px solid ${C.border}`,
            background: "transparent", color: C.muted, fontSize: 13, cursor: "pointer",
          }}
        >
          登出
        </button>
      </div>

      {/* Stats bar */}
      <div style={{ padding: "16px 24px", display: "flex", gap: 12, flexWrap: "wrap" }}>
        {[
          { label: "商品監控中", value: data.stats.totalProducts, color: C.hermes },
          { label: "CDN 追蹤", value: data.stats.cdnTracking, color: C.primary },
          { label: "CDN 已通知", value: data.stats.cdnNotified, color: C.success },
          { label: "補貨記錄", value: data.stats.totalRestocks, color: C.warning },
          { label: "訂閱者", value: data.stats.totalSubscribers, color: C.blueberry },
        ].map((s) => (
          <div key={s.label} style={{
            background: C.card, borderRadius: 12, padding: "12px 20px",
            border: `1px solid ${C.border}`, flex: "1 1 140px", minWidth: 140,
          }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: s.color }}>{s.value.toLocaleString()}</div>
            <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Main content: left + right panels */}
      <div style={{ padding: "0 24px 24px", display: "flex", gap: 20, alignItems: "flex-start" }}>

        {/* ═══ LEFT: Subscribers ═══ */}
        <div style={{ flex: "0 0 380px", minWidth: 320 }}>
          <div style={{
            background: C.card, borderRadius: 16, border: `1px solid ${C.border}`,
            overflow: "hidden",
          }}>
            {/* Header */}
            <div style={{
              padding: "16px 20px", borderBottom: `1px solid ${C.border}`,
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: C.text, margin: 0 }}>👥 訂閱用戶</h2>
              <button
                onClick={() => setShowAddSub(true)}
                style={{
                  padding: "6px 14px", borderRadius: 8, border: "none",
                  background: C.primary, color: "#fff", fontSize: 13, fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                + 新增
              </button>
            </div>

            {/* Search */}
            <div style={{ padding: "12px 20px" }}>
              <input
                placeholder="搜尋用戶名稱或 LINE ID..."
                value={subSearch}
                onChange={(e) => setSubSearch(e.target.value)}
                style={{
                  width: "100%", padding: "10px 14px", borderRadius: 8,
                  border: `1px solid ${C.border}`, fontSize: 13, outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            {/* Add subscriber form */}
            {showAddSub && (
              <div style={{ padding: "0 20px 16px", borderBottom: `1px solid ${C.border}` }}>
                <input
                  placeholder="用戶名稱"
                  value={addSubForm.name}
                  onChange={(e) => setAddSubForm({ ...addSubForm, name: e.target.value })}
                  style={{
                    width: "100%", padding: "10px 14px", borderRadius: 8,
                    border: `1px solid ${C.border}`, fontSize: 13, marginBottom: 8,
                    outline: "none", boxSizing: "border-box",
                  }}
                />
                <input
                  placeholder="LINE User ID"
                  value={addSubForm.lineUserId}
                  onChange={(e) => setAddSubForm({ ...addSubForm, lineUserId: e.target.value })}
                  style={{
                    width: "100%", padding: "10px 14px", borderRadius: 8,
                    border: `1px solid ${C.border}`, fontSize: 13, marginBottom: 8,
                    outline: "none", boxSizing: "border-box",
                  }}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    onClick={handleAddSubscriber}
                    style={{
                      flex: 1, padding: "8px", borderRadius: 8, border: "none",
                      background: C.success, color: "#fff", fontSize: 13, cursor: "pointer",
                    }}
                  >
                    確認新增
                  </button>
                  <button
                    onClick={() => setShowAddSub(false)}
                    style={{
                      padding: "8px 14px", borderRadius: 8, border: `1px solid ${C.border}`,
                      background: "transparent", color: C.muted, fontSize: 13, cursor: "pointer",
                    }}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            {/* Subscriber list */}
            <div style={{ maxHeight: 600, overflowY: "auto" }}>
              {filteredSubs.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: C.muted, fontSize: 14 }}>
                  {data.subscribers.length === 0 ? "還沒有訂閱用戶" : "找不到符合的用戶"}
                </div>
              ) : (
                filteredSubs.map((sub) => (
                  <div
                    key={sub.id}
                    onClick={() => setSelectedSubId(sub.id)}
                    style={{
                      padding: "14px 20px", cursor: "pointer",
                      borderBottom: `1px solid ${C.border}`,
                      background: selectedSubId === sub.id ? "#FFF8F0" : "transparent",
                      borderLeft: selectedSubId === sub.id ? `3px solid ${C.primary}` : "3px solid transparent",
                      transition: "all .15s",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: C.text }}>{sub.name}</div>
                        <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                          {sub.lineUserId.slice(0, 12)}... · {sub.subscribedProducts.length} 個訂閱
                        </div>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteSubscriber(sub.id, sub.name); }}
                        style={{
                          padding: "4px 8px", borderRadius: 6, border: `1px solid ${C.border}`,
                          background: "transparent", color: C.danger, fontSize: 12, cursor: "pointer",
                        }}
                      >
                        刪除
                      </button>
                    </div>

                    {/* Subscription badges */}
                    {selectedSubId === sub.id && (
                      <div style={{ marginTop: 12, display: "flex", flexWrap: "wrap", gap: 6 }}>
                        {data.sources.map((src) => {
                          const subscribed = sub.subscribedProducts.includes(src.id);
                          return (
                            <button
                              key={src.id}
                              onClick={(e) => {
                                e.stopPropagation();
                                handleToggleSubscription(sub.id, src.id, subscribed);
                              }}
                              style={{
                                padding: "4px 10px", borderRadius: 20, fontSize: 12,
                                border: subscribed ? "none" : `1px dashed ${C.subtle}`,
                                background: subscribed ? (src.type === "hermes" ? "#FFF0E0" : "#E8F0FF") : "transparent",
                                color: subscribed ? (src.type === "hermes" ? C.hermes : C.blueberry) : C.muted,
                                cursor: "pointer", fontWeight: subscribed ? 600 : 400,
                              }}
                            >
                              {sourceIcon(src.type)} {src.name} {subscribed ? "✓" : "+"}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* ═══ RIGHT: Monitor Sources + Products ═══ */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Source cards */}
          <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
            {data.sources.map((src) => (
              <div
                key={src.id}
                onClick={() => setSelectedSourceId(src.id)}
                style={{
                  flex: "1 1 240px", minWidth: 240,
                  background: C.card, borderRadius: 14, padding: "16px 20px",
                  border: selectedSourceId === src.id ? `2px solid ${C.primary}` : `1px solid ${C.border}`,
                  cursor: "pointer", transition: "all .15s",
                  opacity: src.enabled ? 1 : 0.5,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
                    {sourceIcon(src.type)} {src.name}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleToggleSource(src.id, src.enabled); }}
                    style={{
                      padding: "3px 10px", borderRadius: 12, fontSize: 11, border: "none",
                      background: src.enabled ? "#E8F5E9" : "#FFEBEE",
                      color: src.enabled ? "#2E7D32" : "#C62828",
                      cursor: "pointer", fontWeight: 600,
                    }}
                  >
                    {src.enabled ? "啟用中" : "已停用"}
                  </button>
                </div>

                <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>掃描頻率</span>
                    {editingInterval === src.id ? (
                      <span style={{ display: "flex", gap: 4 }}>
                        <input
                          value={intervalValue}
                          onChange={(e) => setIntervalValue(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                          style={{ width: 60, padding: "2px 6px", borderRadius: 4, border: `1px solid ${C.border}`, fontSize: 12 }}
                          placeholder="秒"
                        />
                        <button
                          onClick={(e) => { e.stopPropagation(); handleUpdateInterval(src.id); }}
                          style={{ fontSize: 11, color: C.success, background: "none", border: "none", cursor: "pointer", fontWeight: 700 }}
                        >
                          ✓
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); setEditingInterval(null); }}
                          style={{ fontSize: 11, color: C.danger, background: "none", border: "none", cursor: "pointer" }}
                        >
                          ✕
                        </button>
                      </span>
                    ) : (
                      <span
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingInterval(src.id);
                          setIntervalValue(String(src.scanInterval));
                        }}
                        style={{ color: C.primary, cursor: "pointer", fontWeight: 600, borderBottom: `1px dashed ${C.primary}` }}
                      >
                        {formatInterval(src.scanInterval)} ✏️
                      </span>
                    )}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>訂閱者</span>
                    <span style={{ fontWeight: 600, color: C.text }}>{src.subscribers.length} 人</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>最後掃描</span>
                    <span>{timeAgo(src.lastScan)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Selected source detail */}
          {selectedSource && (
            <div style={{
              background: C.card, borderRadius: 16, border: `1px solid ${C.border}`,
              overflow: "hidden",
            }}>
              <div style={{
                padding: "16px 20px", borderBottom: `1px solid ${C.border}`,
                display: "flex", justifyContent: "space-between", alignItems: "center",
              }}>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: C.text, margin: 0 }}>
                  {sourceIcon(selectedSource.type)} {selectedSource.name} — 訂閱者
                </h2>
              </div>

              {/* Subscribers of this source */}
              <div style={{ padding: "16px 20px" }}>
                {selectedSource.subscribers.length === 0 ? (
                  <div style={{ color: C.muted, fontSize: 14, textAlign: "center", padding: 20 }}>
                    還沒有人訂閱此監控
                  </div>
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {selectedSource.subscribers.map((subId) => {
                      const sub = data.subscribers.find((s) => s.id === subId);
                      if (!sub) return null;
                      return (
                        <div
                          key={subId}
                          style={{
                            padding: "8px 14px", borderRadius: 10,
                            background: "#F9F6F2", border: `1px solid ${C.border}`,
                            fontSize: 13, display: "flex", alignItems: "center", gap: 8,
                          }}
                        >
                          <span style={{ fontWeight: 600, color: C.text }}>{sub.name}</span>
                          <button
                            onClick={() => handleToggleSubscription(subId, selectedSource.id, true)}
                            style={{
                              padding: "2px 6px", borderRadius: 4, fontSize: 11,
                              background: "none", border: `1px solid ${C.danger}`,
                              color: C.danger, cursor: "pointer",
                            }}
                          >
                            移除
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Products list */}
          <div style={{
            background: C.card, borderRadius: 16, border: `1px solid ${C.border}`,
            overflow: "hidden", marginTop: 20,
          }}>
            <div style={{ padding: "16px 20px", borderBottom: `1px solid ${C.border}` }}>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: C.text, margin: 0 }}>
                🛍️ 目前上架商品（{data.products.length}）
              </h2>
            </div>
            <div style={{ maxHeight: 500, overflowY: "auto" }}>
              {data.products.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: C.muted }}>暫無商品資料</div>
              ) : (
                data.products.map((p) => (
                  <div
                    key={p.id}
                    style={{
                      padding: "12px 20px", borderBottom: `1px solid ${C.border}`,
                      display: "flex", alignItems: "center", gap: 14,
                    }}
                  >
                    {p.image && (
                      <img
                        src={p.image}
                        alt={p.name}
                        style={{ width: 50, height: 50, borderRadius: 8, objectFit: "cover", background: "#f5f5f5" }}
                      />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {p.name}
                      </div>
                      <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                        {p.price} · 首次出現 {timeAgo(p.first_seen)}
                      </div>
                    </div>
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        padding: "6px 12px", borderRadius: 8, fontSize: 12,
                        background: C.hermes, color: "#fff", textDecoration: "none",
                        fontWeight: 600, whiteSpace: "nowrap",
                      }}
                    >
                      前往購買
                    </a>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Recent restocks */}
          <div style={{
            background: C.card, borderRadius: 16, border: `1px solid ${C.border}`,
            overflow: "hidden", marginTop: 20,
          }}>
            <div style={{ padding: "16px 20px", borderBottom: `1px solid ${C.border}` }}>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: C.text, margin: 0 }}>
                📋 近期補貨記錄
              </h2>
            </div>
            <div style={{ maxHeight: 300, overflowY: "auto" }}>
              {data.recentRestocks.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: C.muted }}>暫無補貨記錄</div>
              ) : (
                data.recentRestocks.map((r, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "10px 20px", borderBottom: `1px solid ${C.border}`,
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{r.name}</div>
                      <div style={{ fontSize: 12, color: C.muted }}>{r.price}</div>
                    </div>
                    <div style={{ fontSize: 12, color: C.muted }}>{timeAgo(r.timestamp)}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        * { box-sizing: border-box; }
      `}</style>
    </div>
  );
}
