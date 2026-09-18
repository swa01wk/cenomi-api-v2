"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthGuard, useCurrentUser } from "@/components/auth/AuthGuard";
import type { MockNotification } from "@/lib/api/auth-client";
import { logout } from "@/lib/api/auth-client";
import { fetchSalesDataRows, type SalesDataListRow } from "@/lib/api/upload-client";

const STATUS_COLORS: Record<string, { bg: string; text: string; dot: string; border: string }> = {
  pending: { bg: "bg-amber-400/10", text: "text-amber-100", dot: "bg-amber-400", border: "border-amber-400/25" },
  need_reupload: { bg: "bg-red-400/10", text: "text-red-100", dot: "bg-red-400", border: "border-red-400/25" },
  need_supporting_document: { bg: "bg-violet-400/10", text: "text-violet-100", dot: "bg-violet-400", border: "border-violet-400/25" },
  need_audited_sales: { bg: "bg-orange-400/10", text: "text-orange-100", dot: "bg-orange-400", border: "border-orange-400/25" },
};

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3600000);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function periodLabelFromStart(start: string): string {
  const d = new Date(`${start}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return start;
  return d.toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
}

function notificationsFromRows(rows: SalesDataListRow[]): MockNotification[] {
  const notifs: MockNotification[] = [];
  for (const row of rows) {
    const period = row.sales_period_start ?? "";
    const periodLabel = periodLabelFromStart(period);
    const mall = row.property_name || row.mall_name || "Mall";
    const lease = row.lease_code;
    if (row.status === "PENDING") {
      notifs.push({
        id: `live-${lease}-${period}-pending`,
        title: "Sales Data Pending",
        body: `Your ${periodLabel} sales data for Lease ${lease} (${mall}) has not been submitted yet.`,
        leaseCode: lease,
        period,
        periodLabel,
        mallName: mall,
        status: "pending",
        createdAt: row.updated_at || new Date().toISOString(),
      });
    } else if (row.status === "NEED_REUPLOAD") {
      notifs.push({
        id: `live-${lease}-${period}-reupload`,
        title: "Sales Data Needs Re-upload",
        body: `The Sales Data team requested sales data again for Lease ${lease} (${mall}) — ${periodLabel}.`,
        leaseCode: lease,
        period,
        periodLabel,
        mallName: mall,
        status: "need_reupload",
        createdAt: row.updated_at || new Date().toISOString(),
      });
    } else if (row.status === "NEED_UPLOAD_SUPPORTING_DOCUMENT") {
      notifs.push({
        id: `live-${lease}-${period}-supporting`,
        title: "Supporting Document Required",
        body: `The Sales Data team asked for a supporting document for Lease ${lease} (${mall}) — ${periodLabel}.`,
        leaseCode: lease,
        period,
        periodLabel,
        mallName: mall,
        status: "need_supporting_document",
        createdAt: row.updated_at || new Date().toISOString(),
      });
    } else if (row.status === "NEED_UPLOAD_AUDITED_SALES") {
      notifs.push({
        id: `live-${lease}-${period}-audited`,
        title: "Audited Sales Required",
        body: `The Sales Data team asked for audited sales data for Lease ${lease} (${mall}) — ${periodLabel}.`,
        leaseCode: lease,
        period,
        periodLabel,
        mallName: mall,
        status: "need_audited_sales",
        createdAt: row.updated_at || new Date().toISOString(),
      });
    }
  }
  return notifs;
}

function salesDataChatHref(notif: MockNotification): string {
  const params = new URLSearchParams({
    lease: notif.leaseCode,
    period: notif.period,
    periodLabel: notif.periodLabel,
    mall: notif.mallName,
    notificationId: notif.id,
    status: notif.status,
  });
  return `/sales-data-chat?${params.toString()}`;
}

function DashboardContent() {
  const user = useCurrentUser();
  const router = useRouter();
  const [bellOpen, setBellOpen] = useState(false);
  const bellRef = useRef<HTMLDivElement>(null);
  const [liveNotifications, setLiveNotifications] = useState<MockNotification[] | null>(null);

  const isAdmin = user.role === "SALES_DATA_APPROVER";
  const notifications = isAdmin ? [] : (liveNotifications ?? user.notifications);

  useEffect(() => {
    if (isAdmin) return;
    let cancelled = false;
    fetchSalesDataRows()
      .then((rows) => {
        if (!cancelled) setLiveNotifications(notificationsFromRows(rows));
      })
      .catch(() => {
        if (!cancelled) setLiveNotifications(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  useEffect(() => {
    if (!bellOpen) return;
    function onDocClick(e: MouseEvent) {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [bellOpen]);

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <div className="min-h-screen">
      {/* Top nav */}
      <header className="glass-strong sticky top-0 z-40">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-sm font-bold text-[#1A1A2E] shadow-[0_0_18px_rgba(201,168,76,0.4)]">C</div>
            <div>
              <p className="text-sm font-semibold tracking-tight text-white">Cenomi Tenant Central</p>
              <p className="text-[10px] text-white/40">Mall Management Platform</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            {/* Notification bell */}
            {notifications.length > 0 && (
              <div className="relative" ref={bellRef}>
                <button
                  type="button"
                  aria-label="Open notifications"
                  aria-expanded={bellOpen}
                  onClick={() => setBellOpen((open) => !open)}
                  className="w-8 h-8 flex items-center justify-center rounded-full bg-white/10 hover:bg-white/20 border border-white/10 transition"
                >
                  <svg className="w-4 h-4 text-white/80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                </button>
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-[9px] font-bold rounded-full flex items-center justify-center pointer-events-none shadow-[0_0_10px_rgba(239,68,68,0.7)]">
                  {notifications.length}
                </span>
                {bellOpen && (
                  <div className="absolute right-0 top-10 z-50 w-80 glass-strong rounded-2xl overflow-hidden">
                    <div className="px-3 py-2 border-b border-white/10">
                      <p className="text-xs font-semibold text-white/80">Notifications</p>
                    </div>
                    <div className="max-h-80 overflow-y-auto">
                      {notifications.map((notif) => (
                        <button
                          key={notif.id}
                          type="button"
                          onClick={() => {
                            setBellOpen(false);
                            router.push(salesDataChatHref(notif));
                          }}
                          className="block w-full text-left px-3 py-3 hover:bg-white/5 border-b border-white/5 last:border-b-0"
                        >
                          <p className="text-xs font-semibold text-white">{notif.title}</p>
                          <p className="text-[11px] text-white/50 mt-0.5 line-clamp-2">{notif.body}</p>
                          <p className="text-[10px] text-[#C9A84C]/80 mt-1 font-mono">{notif.leaseCode} · {notif.mallName}</p>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-xs font-bold text-[#1A1A2E]">
                {user.label.charAt(0)}
              </div>
              <span className="text-xs text-white/70">{user.label}</span>
            </div>
            <button
              onClick={handleLogout}
              className="text-xs text-white/40 hover:text-white/80 transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Left sidebar + content layout */}
      <div className="max-w-6xl mx-auto flex gap-6 px-6 py-6">
        {/* Sidebar */}
        <aside className="w-52 flex-shrink-0 hidden md:block">
          <nav className="glass rounded-2xl overflow-hidden">
            {[
              { icon: "🏠", label: "Dashboard", href: "/dashboard" },
              { icon: "📊", label: "Sales Data", href: "/sales-data-chat" },
              { icon: "📋", label: "Service Requests", href: null },
              { icon: "🏢", label: "Leasing & Deals", href: null },
              { icon: "💰", label: "Finance", href: null },
            ].map((item) => (
              <button
                key={item.label}
                type="button"
                disabled={!item.href}
                onClick={() => {
                  if (item.href) router.push(item.href);
                }}
                className={`w-full flex items-center gap-2.5 px-4 py-3 text-sm text-left transition-colors ${
                  item.label === "Dashboard"
                    ? "bg-[#C9A84C]/15 text-[#E4C063] font-semibold border-r-2 border-[#C9A84C]"
                    : item.href
                      ? "text-white/60 hover:bg-white/5 hover:text-white/80"
                      : "text-white/20 cursor-not-allowed"
                }`}
              >
                <span>{item.icon}</span>
                {item.label}
              </button>
            ))}
          </nav>
        </aside>

        {/* Main content */}
        <main className="flex-1 space-y-6">
          {/* Welcome banner */}
          <div className="glass-strong rounded-2xl p-5 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-white">Welcome, {user.label}</h1>
              <p className="text-sm text-white/50 mt-1">
                {isAdmin
                  ? "Review and approve sales data submissions from tenants."
                  : "Submit your monthly sales data and track submission status."}
              </p>
            </div>
            <button
              onClick={() => router.push("/sales-data-chat")}
              className="bg-gradient-to-r from-[#C9A84C] to-[#E4C063] hover:opacity-90 text-[#1A1A2E] text-sm font-semibold px-4 py-2 rounded-xl transition shadow-[0_8px_24px_rgba(201,168,76,0.28)]"
            >
              {isAdmin ? "Review Submissions →" : "Open AI Assistant →"}
            </button>
          </div>

          {/* Notifications section — tenant only */}
          {!isAdmin && notifications.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-white/90 flex items-center gap-2">
                  <span className="w-2 h-2 bg-red-400 rounded-full animate-pulse shadow-[0_0_8px_rgba(248,113,113,0.8)]" />
                  Action Required — Sales Data Pending
                </h2>
                <span className="text-xs text-white/35">{notifications.length} notification{notifications.length !== 1 ? "s" : ""}</span>
              </div>

              <div className="space-y-3">
                {notifications.map((notif) => {
                  const colors = STATUS_COLORS[notif.status] ?? STATUS_COLORS.pending;
                  return (
                    <Link
                      key={notif.id}
                      href={salesDataChatHref(notif)}
                      className={`glass ${colors.bg} border ${colors.border} rounded-2xl p-4 flex items-start justify-between gap-4 hover:border-[#C9A84C]/40 transition-all cursor-pointer`}
                    >
                      <div className="flex items-start gap-3">
                        <div className={`w-2.5 h-2.5 ${colors.dot} rounded-full mt-1.5 flex-shrink-0 shadow-[0_0_8px_currentColor]`} />
                        <div>
                          <p className="text-sm font-semibold text-white">{notif.title}</p>
                          <p className="text-xs text-white/55 mt-0.5">{notif.body}</p>
                          <div className="flex items-center gap-3 mt-2">
                            <span className="text-xs font-mono bg-white/10 border border-white/15 px-2 py-0.5 rounded-lg text-white/80">
                              {notif.leaseCode}
                            </span>
                            <span className="text-xs text-white/45">{notif.mallName}</span>
                            <span className="text-xs text-white/30">{timeAgo(notif.createdAt)}</span>
                          </div>
                        </div>
                      </div>

                      <span className="flex-shrink-0 bg-gradient-to-r from-[#C9A84C] to-[#E4C063] hover:opacity-90 text-[#1A1A2E] text-xs font-semibold px-3 py-1.5 rounded-xl transition whitespace-nowrap">
                        {notif.status === "need_audited_sales"
                          ? "Upload audited sales →"
                          : notif.status === "need_supporting_document"
                            ? "Upload supporting doc →"
                            : notif.status === "need_reupload"
                              ? "Re-upload sales data →"
                              : "Submit Now →"}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>
          )}

          {/* Admin quick-actions */}
          {isAdmin && (
            <section>
              <h2 className="text-sm font-semibold text-white/90 mb-3">Quick Actions</h2>
              <div className="grid grid-cols-2 gap-4">
                {[
                  { icon: "📋", label: "Review Pending Submissions", desc: "Approve or Ask upload", action: () => router.push("/sales-data-chat") },
                  { icon: "✅", label: "Approve / Ask Upload", desc: "Sales data, supporting doc, audited sales", action: () => router.push("/sales-data-chat") },
                ].map((card) => (
                  <button
                    key={card.label}
                    onClick={card.action}
                    className="text-left glass rounded-2xl p-4 hover:border-[#C9A84C]/40 transition-all"
                  >
                    <span className="text-2xl mb-2 block">{card.icon}</span>
                    <p className="text-sm font-semibold text-white">{card.label}</p>
                    <p className="text-xs text-white/40 mt-1">{card.desc}</p>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Status summary cards */}
          <section>
            <h2 className="text-sm font-semibold text-white/90 mb-3">Sales Data Overview</h2>
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Pending", count: isAdmin ? 3 : 2, color: "text-amber-300" },
                { label: "Submitted", count: isAdmin ? 2 : 0, color: "text-sky-300" },
                { label: "Approved", count: isAdmin ? 5 : 0, color: "text-emerald-300" },
              ].map((stat) => (
                <div key={stat.label} className="glass rounded-2xl p-4 text-center">
                  <p className={`text-2xl font-semibold ${stat.color}`}>{stat.count}</p>
                  <p className="text-xs text-white/40 mt-1">{stat.label}</p>
                </div>
              ))}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
