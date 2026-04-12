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

/* ═══════════════ Helpers ═══════════════ */

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
  if (type === "hermes") return "\u{1F9E1}";
  if (type === "blueberry") return "\u{1FAD0}";
  return "\u{1F4E6}";
}

function sourceColor(type: string): string {
  if (type === "hermes") return "#F37021";
  if (type === "blueberry") return "#4A6FA5";
  return "#8B6914";
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分鐘`;
  return `${(seconds / 3600).toFixed(1).replace(/\.0$/, "")} 小時`;
}

function avatarInitial(name: string): string {
  return name.charAt(0).toUpperCase();
}

const INTERVAL_PRESETS = [
  { label: "10 秒", value: 10 },
  { label: "30 秒", value: 30 },
  { label: "1 分鐘", value: 60 },
  { label: "5 分鐘", value: 300 },
  { label: "10 分鐘", value: 600 },
  { label: "30 分鐘", value: 1800 },
  { label: "1 小時", value: 3600 },
  { label: "6 小時", value: 21600 },
  { label: "1 天", value: 86400 },
];

/* ═══════════════ Component ═══════════════ */

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [data, setData] = useState<AdminData | null>(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  // Modals
  const [userModal, setUserModal] = useState<Subscriber | null>(null);
  const [sourceModal, setSourceModal] = useState<MonitorSource | null>(null);
  const [productModal, setProductModal] = useState<Product | null>(null);

  // Source edit form
  const [editSourceName, setEditSourceName] = useState("");
  const [editSourceInterval, setEditSourceInterval] = useState(60);
  const [editSourceCustom, setEditSourceCustom] = useState("");

  // User rename
  const [editUserName, setEditUserName] = useState("");

  // Search
  const [userSearch, setUserSearch] = useState("");

  // Add subscriber
  const [showAddUser, setShowAddUser] = useState(false);
  const [addName, setAddName] = useState("");
  const [addLineId, setAddLineId] = useState("");

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
      setData(await res.json());
    } catch {
      showToast("載入失敗", "error");
    } finally {
      setLoading(false);
    }
  }, [token]);

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
    if (!addName || !addLineId) { showToast("請填寫完整", "error"); return; }
    const result = await apiPost({ action: "add_subscriber", name: addName, lineUserId: addLineId });
    if (result.success) {
      showToast("已新增用戶");
      setShowAddUser(false);
      setAddName("");
      setAddLineId("");
      refresh();
    } else {
      showToast(result.error, "error");
    }
  };

  const handleDeleteSubscriber = async (id: string, name: string) => {
    if (!confirm(`確定要刪除 ${name} 嗎？`)) return;
    await apiPost({ action: "delete_subscriber", id });
    showToast("已刪除");
    setUserModal(null);
    refresh();
  };

  const handleRenameUser = async () => {
    if (!userModal || !editUserName.trim() || editUserName === userModal.name) return;
    const result = await apiPost({ action: "update_subscriber", id: userModal.id, name: editUserName.trim() });
    if (result.success) {
      showToast("已更新名稱");
      setUserModal({ ...userModal, name: editUserName.trim() });
      refresh();
    } else {
      showToast(result.error || "更新失敗", "error");
    }
  };

  const handleDeleteSource = async (id: string, name: string) => {
    if (!confirm(`確定要刪除監控源「${name}」嗎？所有用戶的訂閱也會一併移除。`)) return;
    await apiPost({ action: "delete_source", id });
    showToast("已刪除監控源");
    setSourceModal(null);
    refresh();
  };

  const handleToggleSub = async (subscriberId: string, sourceId: string, subscribed: boolean) => {
    await apiPost({ action: subscribed ? "unsubscribe" : "subscribe", subscriberId, sourceId });
    refresh();
  };

  const handleSaveSource = async () => {
    if (!sourceModal) return;
    const interval = editSourceCustom ? parseInt(editSourceCustom) : editSourceInterval;
    if (isNaN(interval) || interval < 10) { showToast("間隔至少 10 秒", "error"); return; }
    const result = await apiPost({
      action: "update_source",
      id: sourceModal.id,
      name: editSourceName || sourceModal.name,
      scanInterval: interval,
    });
    if (result.success) {
      showToast("已更新監控源");
      setSourceModal(null);
      refresh();
    }
  };

  const handleToggleSource = async (sourceId: string, enabled: boolean) => {
    await apiPost({ action: "update_source", id: sourceId, enabled: !enabled });
    refresh();
  };

  const openSourceModal = (src: MonitorSource) => {
    setSourceModal(src);
    setEditSourceName(src.name);
    setEditSourceInterval(src.scanInterval);
    setEditSourceCustom("");
  };

  /* ═══════════════ LOGIN ═══════════════ */
  if (!token) {
    return (
      <>
        <style>{globalCSS}</style>
        <div className="login-screen">
          <div className="login-card">
            <div className="logo-big">{"\u{1F4E1}"}</div>
            <h1>盯貨監控後台</h1>
            <p>Hermes + 山丘藍 統一管理</p>
            <input
              type="password"
              placeholder="管理密碼"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            />
            <button onClick={handleLogin}>登入</button>
          </div>
        </div>
      </>
    );
  }

  if (!data) {
    return (
      <>
        <style>{globalCSS}</style>
        <div className="login-screen">
          <div style={{ color: "#8A8A8A", fontSize: 16 }}>{loading ? "載入中..." : "連線失敗"}</div>
        </div>
      </>
    );
  }

  const filteredUsers = data.subscribers.filter(
    (s) => !userSearch || s.name.toLowerCase().includes(userSearch.toLowerCase()) || s.lineUserId.includes(userSearch)
  );

  /* ═══════════════ MAIN ═══════════════ */
  return (
    <>
      <style>{globalCSS}</style>

      {/* Toast */}
      {toast && (
        <div className={`toast show ${toast.type}`}>{toast.type === "success" ? "\u2705" : "\u274C"} {toast.message}</div>
      )}

      {/* Header */}
      <header>
        <div className="brand">
          <div className="logo">{"\u{1F4E1}"}</div>
          <div>
            <h1>盯貨監控後台</h1>
            <div className="stats">
              <span>{"\u{1F6CD}\uFE0F"} {data.stats.totalProducts} 商品</span>
              <span>{"\u00B7"}</span>
              <span>{"\u{1F4E1}"} {data.stats.cdnTracking} SKU</span>
              <span>{"\u00B7"}</span>
              <span>{"\u{1F514}"} {data.stats.cdnNotified} 已通知</span>
              <span>{"\u00B7"}</span>
              <span>{"\u{1F465}"} {data.stats.totalSubscribers} 用戶</span>
            </div>
          </div>
        </div>
        <div className="actions">
          <button className="btn btn-ghost" onClick={() => refresh()} title="重新整理">
            {loading ? <span className="spin">{"\u21BB"}</span> : "\u21BB"}
          </button>
          <button className="btn btn-ghost btn-small" onClick={() => { localStorage.removeItem("monitor_admin_token"); setToken(""); setData(null); }}>
            登出
          </button>
        </div>
      </header>

      <div className="container">
        <div className="grid">

          {/* ═══ LEFT: Users ═══ */}
          <div className="card">
            <div className="card-header">
              <h2><span className="ico">{"\u{1F465}"}</span> 所有用戶</h2>
              <span className="meta">{data.subscribers.length} 人</span>
            </div>

            <div className="user-search">
              <input
                type="text"
                placeholder="搜尋名稱 / LINE ID..."
                value={userSearch}
                onChange={(e) => setUserSearch(e.target.value)}
              />
              <span className="search-ico">{"\u{1F50D}"}</span>
            </div>

            {/* Add user form */}
            {showAddUser && (
              <div style={{ padding: "12px 22px", borderBottom: "1px solid var(--border)", background: "#fafbfc" }}>
                <input
                  type="text" placeholder="用戶名稱" value={addName}
                  onChange={(e) => setAddName(e.target.value)}
                  style={{ width: "100%", padding: "10px 14px", border: "1.5px solid var(--border)", borderRadius: 10, fontSize: 13, marginBottom: 8, fontFamily: "inherit" }}
                />
                <input
                  type="text" placeholder="LINE User ID (U...)" value={addLineId}
                  onChange={(e) => setAddLineId(e.target.value)}
                  style={{ width: "100%", padding: "10px 14px", border: "1.5px solid var(--border)", borderRadius: 10, fontSize: 13, marginBottom: 8, fontFamily: "inherit" }}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-primary btn-small" onClick={handleAddSubscriber} style={{ flex: 1, justifyContent: "center" }}>確認新增</button>
                  <button className="btn btn-secondary btn-small" onClick={() => setShowAddUser(false)}>取消</button>
                </div>
              </div>
            )}

            <div className="card-body" style={{ maxHeight: 600 }}>
              {filteredUsers.length === 0 ? (
                <div className="empty">
                  <span className="ico">{"\u{1F464}"}</span>
                  {data.subscribers.length === 0 ? "尚無用戶" : "找不到符合的用戶"}
                </div>
              ) : (
                filteredUsers.map((sub) => (
                  <div key={sub.id} className="user-row" onClick={() => { setUserModal(sub); setEditUserName(sub.name); }}>
                    <div className="avatar" style={{ background: `linear-gradient(135deg, ${sourceColor(sub.subscribedProducts.length > 0 ? "hermes" : "custom")}, ${sourceColor("blueberry")})` }}>
                      {avatarInitial(sub.name)}
                    </div>
                    <div className="user-info">
                      <div className="user-name">{sub.name}</div>
                      <div className="user-id">{sub.lineUserId}</div>
                      <div className="user-routes">
                        {sub.subscribedProducts.length === 0 ? (
                          <span className="tag tag-empty">未訂閱</span>
                        ) : (
                          sub.subscribedProducts.map((srcId) => {
                            const src = data.sources.find((s) => s.id === srcId);
                            return (
                              <span key={srcId} className="tag tag-primary" style={{ background: `${sourceColor(src?.type || "custom")}18`, color: sourceColor(src?.type || "custom") }}>
                                {sourceIcon(src?.type || "custom")} {src?.name || srcId}
                              </span>
                            );
                          })
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Bottom bar: add user */}
            <div style={{ padding: "12px 22px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "center" }}>
              <button className="btn btn-primary btn-small" onClick={() => setShowAddUser(true)}>+ 新增用戶</button>
            </div>
          </div>

          {/* ═══ RIGHT: Sources ═══ */}
          <div>
            {/* Monitor source cards */}
            <div className="card">
              <div className="card-header">
                <h2><span className="ico">{"\u{1F4E1}"}</span> 監控源</h2>
                <span className="meta">{data.sources.length} 項</span>
              </div>
              <div className="card-body" style={{ maxHeight: "none" }}>
                {data.sources.map((src) => (
                  <div key={src.id} className="route-card" onClick={() => openSourceModal(src)}>
                    <div className="route-title" style={{ color: sourceColor(src.type) }}>
                      {sourceIcon(src.type)} {src.name}
                      {!src.enabled && <span style={{ fontSize: 11, color: "#C62828", marginLeft: 8, fontWeight: 500 }}>({"\u5DF2\u505C\u7528"})</span>}
                    </div>
                    <div className="route-meta">
                      <span className="dot" style={{ background: src.enabled ? "var(--success)" : "var(--danger)" }} />
                      每 {formatInterval(src.scanInterval)} 掃描
                      <span style={{ margin: "0 4px" }}>{"\u00B7"}</span>
                      最後掃描：{timeAgo(src.lastScan)}
                    </div>
                    <div className="route-subscribers">
                      {src.subscribers.length === 0 ? (
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>尚無訂閱者</span>
                      ) : (
                        src.subscribers.map((subId) => {
                          const sub = data.subscribers.find((s) => s.id === subId);
                          if (!sub) return null;
                          return (
                            <span key={subId} className="sub-chip">
                              <span className="mini-avatar" style={{ background: sourceColor(src.type) }}>{avatarInitial(sub.name)}</span>
                              {sub.name}
                            </span>
                          );
                        })
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Products */}
            <div className="card" style={{ marginTop: 20 }}>
              <div className="card-header">
                <h2><span className="ico">{"\u{1F6CD}\uFE0F"}</span> 目前上架商品</h2>
                <span className="meta">{data.products.length} 件</span>
              </div>
              <div className="card-body" style={{ maxHeight: 500 }}>
                {data.products.length === 0 ? (
                  <div className="empty"><span className="ico">{"\u{1F6CD}\uFE0F"}</span>暫無商品</div>
                ) : (
                  data.products.map((p) => (
                    <div key={p.id} className="product-row" onClick={() => setProductModal(p)}>
                      {p.image && (
                        <img src={p.image} alt={p.name} className="product-img" />
                      )}
                      <div className="product-info">
                        <div className="product-name">{p.name}</div>
                        <div className="product-price">{p.price}</div>
                        <div className="product-meta">首次出現 {timeAgo(p.first_seen)}</div>
                      </div>
                      <a
                        href={p.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn btn-primary btn-small"
                        onClick={(e) => e.stopPropagation()}
                      >
                        前往購買
                      </a>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Recent restocks */}
            <div className="card" style={{ marginTop: 20 }}>
              <div className="card-header">
                <h2><span className="ico">{"\u{1F4CB}"}</span> 補貨記錄</h2>
                <span className="meta">{data.recentRestocks.length} 筆</span>
              </div>
              <div className="card-body" style={{ maxHeight: 300 }}>
                {data.recentRestocks.length === 0 ? (
                  <div className="empty"><span className="ico">{"\u{1F4CB}"}</span>暫無記錄</div>
                ) : (
                  data.recentRestocks.map((r, i) => (
                    <div key={i} style={{ padding: "12px 22px", borderBottom: "1px solid #f3f4f6", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>{r.name}</div>
                        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{r.price}</div>
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{timeAgo(r.timestamp)}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ═══ User Modal ═══ */}
      {userModal && (
        <div className="modal-bg show" onClick={(e) => { if (e.target === e.currentTarget) setUserModal(null); }}>
          <div className="modal">
            <div className="modal-header">
              <div className="avatar" style={{ background: "linear-gradient(135deg, #F37021, #C47530)" }}>{avatarInitial(userModal.name)}</div>
              <div className="info">
                <div className="name">{userModal.name}</div>
                <div className="uid">{userModal.lineUserId}</div>
              </div>
              <button className="close" onClick={() => setUserModal(null)}>{"\u00D7"}</button>
            </div>
            <div className="modal-body">
              <div className="modal-section">
                <div className="modal-section-title">訂閱管理</div>
                {data.sources.map((src) => {
                  const subscribed = userModal.subscribedProducts.includes(src.id);
                  return (
                    <button
                      key={src.id}
                      className={`modal-item ${subscribed ? "subscribed" : ""}`}
                      onClick={() => {
                        handleToggleSub(userModal.id, src.id, subscribed);
                        setUserModal({
                          ...userModal,
                          subscribedProducts: subscribed
                            ? userModal.subscribedProducts.filter((id) => id !== src.id)
                            : [...userModal.subscribedProducts, src.id],
                        });
                      }}
                    >
                      <span className="ico">{sourceIcon(src.type)}</span>
                      <span className="label">
                        {src.name}
                        <span className="meta">每 {formatInterval(src.scanInterval)} 掃描 · {src.subscribers.length} 人訂閱</span>
                      </span>
                      <span style={{ fontSize: 14 }}>{subscribed ? "\u2705" : "\u2795"}</span>
                    </button>
                  );
                })}
              </div>
              <div className="modal-section">
                <div className="modal-section-title">自訂名稱</div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="text"
                    value={editUserName}
                    onChange={(e) => setEditUserName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleRenameUser()}
                    style={{ flex: 1, padding: "10px 14px", border: "1.5px solid var(--border)", borderRadius: 10, fontSize: 13, fontFamily: "inherit" }}
                  />
                  <button
                    className="btn btn-primary btn-small"
                    onClick={handleRenameUser}
                    disabled={!editUserName.trim() || editUserName === userModal.name}
                    style={{ opacity: (!editUserName.trim() || editUserName === userModal.name) ? 0.4 : 1 }}
                  >
                    儲存
                  </button>
                </div>
              </div>
              <div className="modal-section">
                <div className="modal-section-title">基本資訊</div>
                <div style={{ padding: "10px 16px", background: "#fafbfc", borderRadius: 12, fontSize: 12, color: "var(--text-soft)", lineHeight: 2 }}>
                  <div>LINE ID: <span style={{ fontFamily: "monospace" }}>{userModal.lineUserId}</span></div>
                  <div>加入時間: {new Date(userModal.createdAt).toLocaleString("zh-TW")}</div>
                  <div>訂閱數: {userModal.subscribedProducts.length} 項</div>
                </div>
              </div>
              <div className="modal-section">
                <button
                  className="modal-item danger"
                  onClick={() => handleDeleteSubscriber(userModal.id, userModal.name)}
                >
                  <span className="ico">{"\u{1F5D1}\uFE0F"}</span>
                  <span className="label">刪除此用戶</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Source Modal ═══ */}
      {sourceModal && (
        <div className="modal-bg show" onClick={(e) => { if (e.target === e.currentTarget) setSourceModal(null); }}>
          <div className="modal" style={{ maxWidth: 520 }}>
            <div className="modal-header">
              <div className="avatar" style={{ background: `linear-gradient(135deg, ${sourceColor(sourceModal.type)}, ${sourceColor(sourceModal.type)}88)` }}>
                {sourceIcon(sourceModal.type)}
              </div>
              <div className="info">
                <div className="name">編輯監控源</div>
                <div className="uid">{sourceModal.id}</div>
              </div>
              <button className="close" onClick={() => setSourceModal(null)}>{"\u00D7"}</button>
            </div>
            <div className="modal-body">
              {/* Name */}
              <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 700, marginBottom: 10 }}>{"\u{1F4E1}"} 基本資訊</div>
              <label style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 600, display: "block", marginBottom: 4 }}>顯示名稱</label>
              <input
                type="text"
                value={editSourceName}
                onChange={(e) => setEditSourceName(e.target.value)}
                style={{ width: "100%", padding: "10px 14px", border: "1.5px solid var(--border)", borderRadius: 10, fontSize: 13, marginBottom: 10, fontFamily: "inherit" }}
              />

              {/* Enable/Disable */}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
                <span style={{ fontSize: 12, color: "var(--text-soft)" }}>狀態</span>
                <button
                  className={`btn btn-small ${sourceModal.enabled ? "btn-primary" : "btn-danger"}`}
                  onClick={() => {
                    handleToggleSource(sourceModal.id, sourceModal.enabled);
                    setSourceModal({ ...sourceModal, enabled: !sourceModal.enabled });
                  }}
                >
                  {sourceModal.enabled ? "\u2705 啟用中" : "\u23F8\uFE0F 已停用"}
                </button>
              </div>

              <div style={{ borderTop: "1px dashed var(--border)", margin: "14px 0" }} />

              {/* Interval */}
              <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 700, marginBottom: 8 }}>{"\u23F1\uFE0F"} 檢查頻率</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
                CDN 預警建議 10~30 秒，官網掃描建議 1~5 分鐘
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.value}
                    className={`modal-item ${editSourceInterval === p.value && !editSourceCustom ? "subscribed" : ""}`}
                    style={{ padding: "10px 12px", marginBottom: 0, justifyContent: "center", textAlign: "center" }}
                    onClick={() => { setEditSourceInterval(p.value); setEditSourceCustom(""); }}
                  >
                    <span className="label" style={{ textAlign: "center" }}>{p.label}</span>
                  </button>
                ))}
              </div>
              <div style={{ marginTop: 12 }}>
                <label style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 600, display: "block", marginBottom: 4 }}>自訂秒數</label>
                <input
                  type="number"
                  min={10}
                  placeholder="例：120"
                  value={editSourceCustom}
                  onChange={(e) => setEditSourceCustom(e.target.value)}
                  style={{ width: "100%", padding: "10px 14px", border: "1.5px solid var(--border)", borderRadius: 10, fontSize: 13, fontFamily: "inherit" }}
                />
              </div>

              <div style={{ borderTop: "1px dashed var(--border)", margin: "18px 0" }} />

              {/* Subscribers */}
              <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 700, marginBottom: 8 }}>{"\u{1F465}"} 訂閱此監控的用戶</div>
              {sourceModal.subscribers.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 0" }}>尚無訂閱者</div>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                  {sourceModal.subscribers.map((subId) => {
                    const sub = data.subscribers.find((s) => s.id === subId);
                    if (!sub) return null;
                    return (
                      <span key={subId} className="sub-chip">
                        <span className="mini-avatar" style={{ background: sourceColor(sourceModal.type) }}>{avatarInitial(sub.name)}</span>
                        {sub.name}
                        <span
                          className="x"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleToggleSub(subId, sourceModal.id, true);
                            setSourceModal({
                              ...sourceModal,
                              subscribers: sourceModal.subscribers.filter((id) => id !== subId),
                            });
                          }}
                        >{"\u00D7"}</span>
                      </span>
                    );
                  })}
                </div>
              )}

              <div style={{ borderTop: "1px dashed var(--border)", margin: "18px 0" }} />

              <button className="btn btn-primary" onClick={handleSaveSource} style={{ width: "100%", justifyContent: "center", padding: 14 }}>
                {"\u{1F4BE}"} 儲存所有變更
              </button>

              <div style={{ borderTop: "1px dashed var(--border)", margin: "18px 0" }} />

              <button
                className="btn btn-danger"
                onClick={() => handleDeleteSource(sourceModal.id, sourceModal.name)}
                style={{ width: "100%", justifyContent: "center", padding: 14 }}
              >
                {"\u{1F5D1}\uFE0F"} 刪除此監控源
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Product Modal ═══ */}
      {productModal && (
        <div className="modal-bg show" onClick={(e) => { if (e.target === e.currentTarget) setProductModal(null); }}>
          <div className="modal" style={{ maxWidth: 480 }}>
            <div className="modal-header">
              <div className="avatar" style={{ background: "linear-gradient(135deg, #F37021, #E8A860)" }}>{"\u{1F6CD}\uFE0F"}</div>
              <div className="info">
                <div className="name">{productModal.name}</div>
                <div className="uid">{productModal.price}</div>
              </div>
              <button className="close" onClick={() => setProductModal(null)}>{"\u00D7"}</button>
            </div>
            <div className="modal-body">
              {productModal.image && (
                <div style={{ textAlign: "center", marginBottom: 16 }}>
                  <img
                    src={productModal.image}
                    alt={productModal.name}
                    style={{ maxWidth: "100%", maxHeight: 300, borderRadius: 12, objectFit: "contain", background: "#f9f9f9" }}
                  />
                </div>
              )}
              <div style={{ padding: "14px 16px", background: "#fafbfc", borderRadius: 12, fontSize: 13, color: "var(--text-soft)", lineHeight: 2, marginBottom: 16 }}>
                <div><strong>名稱：</strong>{productModal.name}</div>
                <div><strong>價格：</strong>{productModal.price}</div>
                <div><strong>首次出現：</strong>{new Date(productModal.first_seen).toLocaleString("zh-TW")}</div>
                <div><strong>商品 ID：</strong><span style={{ fontFamily: "monospace", fontSize: 11 }}>{productModal.id}</span></div>
              </div>
              <a
                href={productModal.url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary"
                style={{ width: "100%", justifyContent: "center", padding: 14, textDecoration: "none" }}
              >
                {"\u{1F6D2}"} 前往購買
              </a>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ═══════════════ CSS ═══════════════ */

const globalCSS = `
:root {
  --primary: #C47530;
  --primary-light: #E8A860;
  --primary-strong: #A35D1A;
  --accent: #F37021;
  --accent-soft: #FFF0E0;
  --success: #7FA07C;
  --success-strong: #5D8A59;
  --danger: #B87070;
  --danger-strong: #A05555;
  --bg: #F5F1EB;
  --surface: #FFFFFF;
  --border: #E8E0D8;
  --text: #3A3A3A;
  --text-soft: #6E6661;
  --text-muted: #9C9389;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei", "Helvetica Neue", sans-serif;
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}
body {
  background: linear-gradient(135deg, #C47530 0%, #E8A860 50%, #F37021 100%);
  min-height: 100vh;
  background-attachment: fixed;
  background-size: 200% 200%;
  animation: bg-drift 20s ease infinite;
}
@keyframes bg-drift { 0%,100% { background-position: 0% 50% } 50% { background-position: 100% 50% } }

.container { max-width: 1280px; margin: 0 auto; padding: 24px; }

/* Header */
header {
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 16px; padding: 16px 24px;
}
header .brand { display: flex; align-items: center; gap: 14px; }
header .logo {
  width: 48px; height: 48px; background: rgba(255,255,255,.15);
  backdrop-filter: blur(20px); border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; border: 1px solid rgba(255,255,255,.2);
}
header h1 { color: #fff; font-size: 22px; font-weight: 700; letter-spacing: -.3px; }
header .stats { color: rgba(255,255,255,.7); font-size: 12px; margin-top: 2px; display: flex; gap: 10px; flex-wrap: wrap; }
header .stats span { display: inline-flex; align-items: center; gap: 4px; }
header .actions { display: flex; gap: 8px; }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 9px 16px; border-radius: 10px; border: none; cursor: pointer;
  font-size: 13px; font-weight: 600; transition: all .15s; font-family: inherit;
  text-decoration: none; white-space: nowrap;
}
.btn:active { transform: scale(.97); }
.btn-primary { background: var(--primary-strong); color: #fff; box-shadow: 0 2px 10px rgba(196,117,48,.4); }
.btn-primary:hover { background: #8A4E12; box-shadow: 0 4px 14px rgba(196,117,48,.5); }
.btn-secondary { background: rgba(255,255,255,.96); color: var(--primary-strong); }
.btn-secondary:hover { background: #fff; }
.btn-ghost { background: rgba(255,255,255,.18); color: #fff; border: 1px solid rgba(255,255,255,.3); backdrop-filter: blur(10px); }
.btn-ghost:hover { background: rgba(255,255,255,.28); }
.btn-danger { background: var(--danger-strong); color: #fff; }
.btn-danger:hover { background: #8D5757; }
.btn-small { padding: 6px 12px; font-size: 12px; border-radius: 8px; }

/* Grid */
.grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; }
@media(max-width:900px) { .grid { grid-template-columns: 1fr; } }

/* Card */
.card {
  background: var(--surface); border-radius: 18px;
  box-shadow: 0 8px 32px rgba(0,0,0,.12); overflow: hidden;
  border: 1px solid rgba(255,255,255,.5);
  animation: card-in .4s cubic-bezier(.16,1,.3,1) both;
}
.grid .card:nth-child(1) { animation-delay: .05s; }
.grid .card:nth-child(2) { animation-delay: .12s; }
@keyframes card-in { from { transform: translateY(16px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
.card-header {
  padding: 18px 22px; border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
  background: linear-gradient(180deg, #fafbfc, #fff);
}
.card-header h2 { font-size: 15px; color: var(--text); font-weight: 700; display: flex; align-items: center; gap: 8px; }
.card-header h2 .ico { font-size: 18px; }
.card-header .meta { font-size: 11px; color: var(--text-muted); font-weight: 600; }
.card-body { padding: 0; overflow-y: auto; }
.card-body::-webkit-scrollbar { width: 6px; }
.card-body::-webkit-scrollbar-track { background: transparent; }
.card-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Empty */
.empty { padding: 60px 20px; text-align: center; color: var(--text-muted); font-size: 13px; }
.empty .ico { font-size: 48px; display: block; margin-bottom: 12px; opacity: .4; }

/* User row */
.user-row {
  padding: 14px 22px; border-bottom: 1px solid #f3f4f6;
  display: flex; align-items: center; gap: 14px;
  transition: background .12s, transform .15s; cursor: pointer;
}
.user-row:hover { background: #fafbfc; transform: translateX(2px); }
.user-row:last-child { border-bottom: none; }
.avatar {
  width: 44px; height: 44px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 700; font-size: 16px; flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(196,117,48,.35);
}
.user-info { flex: 1; min-width: 0; }
.user-name { font-size: 14px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px; }
.user-id { font-size: 10px; color: var(--text-muted); font-family: 'SF Mono', Monaco, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.user-routes { margin-top: 6px; display: flex; gap: 4px; flex-wrap: wrap; }

.tag {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 12px; font-size: 10px; font-weight: 600;
}
.tag-primary { background: var(--accent-soft); color: var(--accent); }
.tag-empty { background: #f3f4f6; color: var(--text-muted); }

/* User search */
.user-search { padding: 10px 22px 0; position: relative; }
.user-search input {
  width: 100%; padding: 10px 14px 10px 34px; border: 1.5px solid var(--border);
  border-radius: 10px; font-size: 13px; font-family: inherit; transition: all .15s; background: #fafbfc;
}
.user-search input:focus { outline: none; border-color: var(--primary); background: #fff; }
.user-search .search-ico { position: absolute; right: 36px; top: 22px; font-size: 12px; pointer-events: none; color: var(--text-muted); }

/* Route/Source card */
.route-card {
  padding: 18px 22px; border-bottom: 1px solid #f3f4f6;
  transition: background .12s, transform .15s, box-shadow .15s; cursor: pointer;
}
.route-card:hover { background: #fafbfc; transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,.04); }
.route-card:last-child { border-bottom: none; }
.route-title { font-size: 15px; font-weight: 700; margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }
.route-meta { font-size: 11px; color: var(--text-muted); margin-top: 6px; display: flex; align-items: center; gap: 6px; }
.route-meta .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; animation: pulse-dot 2s ease-in-out infinite; }
@keyframes pulse-dot { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .5; transform: scale(.8); } }
.route-subscribers { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.sub-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 10px 5px 5px; background: #f0f6ff; border-radius: 14px;
  font-size: 11px; color: var(--primary-strong); font-weight: 600;
}
.sub-chip .mini-avatar {
  width: 18px; height: 18px; border-radius: 50%; color: #fff;
  font-size: 9px; display: flex; align-items: center; justify-content: center;
}
.sub-chip .x { cursor: pointer; color: var(--danger); font-weight: bold; margin-left: 2px; padding: 0 4px; border-radius: 4px; }
.sub-chip .x:hover { background: #fee2e2; }

/* Product row */
.product-row {
  padding: 14px 22px; border-bottom: 1px solid #f3f4f6;
  display: flex; align-items: center; gap: 14px; cursor: pointer;
  transition: background .12s;
}
.product-row:hover { background: #fafbfc; }
.product-img { width: 60px; height: 60px; border-radius: 10px; object-fit: cover; background: #f5f5f5; flex-shrink: 0; }
.product-info { flex: 1; min-width: 0; }
.product-name { font-size: 14px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.product-price { font-size: 14px; font-weight: 700; color: var(--accent); margin-top: 2px; }
.product-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

/* Login */
.login-screen { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
.login-card {
  background: rgba(255,255,255,.98); backdrop-filter: blur(20px);
  border-radius: 24px; padding: 40px; box-shadow: 0 20px 60px rgba(0,0,0,.3);
  max-width: 400px; width: 100%; border: 1px solid rgba(255,255,255,.5);
  animation: login-in .5s cubic-bezier(.16,1,.3,1); text-align: center;
}
@keyframes login-in { from { transform: scale(.9) translateY(30px); opacity: 0; } to { transform: scale(1) translateY(0); opacity: 1; } }
.login-card .logo-big {
  width: 72px; height: 72px; background: linear-gradient(135deg, #C47530, #F37021);
  border-radius: 20px; display: flex; align-items: center; justify-content: center;
  font-size: 36px; margin: 0 auto 20px; box-shadow: 0 8px 24px rgba(196,117,48,.4);
}
.login-card h1 { font-size: 22px; color: var(--text); margin-bottom: 6px; font-weight: 700; }
.login-card p { font-size: 13px; color: var(--text-soft); margin-bottom: 28px; }
.login-card input {
  width: 100%; padding: 14px 16px; border: 1.5px solid var(--border); border-radius: 12px;
  font-size: 14px; margin-bottom: 14px; font-family: inherit; transition: border-color .15s;
}
.login-card input:focus { outline: none; border-color: var(--primary); }
.login-card button {
  width: 100%; padding: 14px; background: var(--primary-strong); color: #fff;
  border: none; border-radius: 12px; font-size: 14px; font-weight: 700;
  cursor: pointer; font-family: inherit; transition: all .15s;
}
.login-card button:hover { background: #8A4E12; transform: translateY(-1px); box-shadow: 0 8px 24px rgba(196,117,48,.45); }

/* Toast */
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(20px);
  padding: 16px 24px; border-radius: 14px; font-size: 14px; font-weight: 600;
  opacity: 0; transition: all .3s cubic-bezier(.16,1,.3,1); z-index: 1100;
  pointer-events: none; display: flex; align-items: center; gap: 8px;
}
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
.toast.success { background: var(--success-strong); color: #fff; box-shadow: 0 12px 36px rgba(93,138,89,.45); }
.toast.error { background: var(--danger-strong); color: #fff; box-shadow: 0 12px 36px rgba(160,85,85,.45); }

/* Modal */
.modal-bg {
  display: none; position: fixed; inset: 0;
  background: rgba(58,46,38,.55); backdrop-filter: blur(6px); z-index: 1000;
  align-items: center; justify-content: center; padding: 20px;
}
.modal-bg.show { display: flex; animation: fade-in .2s ease-out; }
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
.modal {
  background: #fff; border-radius: 20px; max-width: 440px; width: 100%; max-height: 88vh;
  overflow-y: auto; box-shadow: 0 24px 80px rgba(196,117,48,.3);
  animation: modal-in .3s cubic-bezier(.16,1,.3,1);
  border-top: 4px solid var(--primary-strong);
}
@keyframes modal-in { from { transform: scale(.92) translateY(20px); opacity: 0; } to { transform: scale(1) translateY(0); opacity: 1; } }
.modal-header {
  padding: 22px 24px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 14px;
}
.modal-header .info { flex: 1; min-width: 0; }
.modal-header .name { font-size: 15px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.modal-header .uid { font-size: 10px; color: var(--text-muted); font-family: 'SF Mono', Monaco, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.modal-header .close {
  background: #f3f4f6; border: none; width: 32px; height: 32px; border-radius: 10px;
  font-size: 18px; color: var(--text-soft); cursor: pointer; display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; transition: all .15s;
}
.modal-header .close:hover { background: #e5e7eb; color: var(--text); }
.modal-body { padding: 18px 20px; }
.modal-section { margin-bottom: 14px; }
.modal-section:last-child { margin-bottom: 0; }
.modal-section-title { font-size: 11px; color: var(--text-muted); font-weight: 700; padding: 0 4px 8px; text-transform: uppercase; letter-spacing: .5px; }
.modal-item {
  padding: 14px 16px; cursor: pointer; font-size: 13px; border-radius: 12px;
  color: var(--text); display: flex; align-items: center; gap: 12px; text-align: left;
  width: 100%; background: #fafbfc; border: 1.5px solid #f3f4f6; margin-bottom: 8px;
  transition: all .15s; font-family: inherit;
}
.modal-item:hover { background: #f0f6ff; border-color: #cce0f0; transform: translateX(2px); }
.modal-item.subscribed { background: var(--accent-soft); border-color: #FFD4B3; }
.modal-item.subscribed:hover { background: #FFE8D6; }
.modal-item.danger { background: #fef2f2; border-color: #fecaca; color: var(--danger); }
.modal-item.danger:hover { background: #fee2e2; }
.modal-item .ico { font-size: 18px; flex-shrink: 0; width: 24px; text-align: center; }
.modal-item .label { flex: 1; font-weight: 600; line-height: 1.4; }
.modal-item .label .meta { font-size: 10px; color: var(--text-muted); font-weight: 500; display: block; margin-top: 2px; }

/* Spin */
.spin { animation: spin 1s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Mobile */
@media(max-width:768px) {
  .container { padding: 12px; }
  header { margin-bottom: 0; gap: 10px; padding: 12px 16px; }
  header .logo { width: 42px; height: 42px; font-size: 20px; border-radius: 12px; }
  header h1 { font-size: 18px; }
  header .stats { font-size: 11px; flex-wrap: wrap; gap: 6px; }
  .grid { gap: 12px; }
  .card { border-radius: 14px; }
  .card-header { padding: 14px 16px; }
  .card-header h2 { font-size: 14px; }
  .card-body { max-height: none !important; }
  .user-row { padding: 12px 16px; gap: 10px; }
  .avatar { width: 38px; height: 38px; font-size: 14px; }
  .user-name { font-size: 13px; }
  .user-id { font-size: 9px; }
  .route-card { padding: 14px 16px; }
  .route-title { font-size: 14px; }
  .route-meta { font-size: 10px; }
  .sub-chip { font-size: 10px; padding: 4px 8px 4px 4px; }
  .sub-chip .mini-avatar { width: 16px; height: 16px; font-size: 8px; }
  .product-img { width: 48px; height: 48px; }
  .modal-bg { padding: 0; align-items: flex-end; }
  .modal { max-width: 100%; max-height: 90vh; border-radius: 20px 20px 0 0; width: 100%; }
  .modal-header { padding: 18px 18px 14px; position: sticky; top: 0; background: #fff; z-index: 1; border-bottom: 1px solid var(--border); }
  .modal-body { padding: 14px 16px 24px; }
  .modal-item { padding: 14px 14px; font-size: 13px; }
  .btn { min-height: 36px; }
  .btn-small { min-height: 30px; padding: 7px 12px; font-size: 12px; }
}
@media(max-width:380px) {
  .container { padding: 10px; }
  header h1 { font-size: 16px; }
  header .stats { font-size: 10px; }
  .card-header h2 { font-size: 13px; }
  .user-row { padding: 10px 12px; }
  .avatar { width: 34px; height: 34px; font-size: 13px; }
}
`;
