"use client";

import { useRouter } from "next/navigation";
import { AuthGuard, useCurrentUser } from "@/components/auth/AuthGuard";
import type { MockNotification } from "@/lib/api/auth-client";
import { logout } from "@/lib/api/auth-client";

const STATUS_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  pending:     { bg: "bg-yellow-50",  text: "text-yellow-800", dot: "bg-yellow-400" },
  need_reupload: { bg: "bg-red-50",   text: "text-red-700",    dot: "bg-red-500" },
};

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3600000);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function DashboardContent() {
  const user = useCurrentUser();
  const router = useRouter();

  function handleNotificationClick(notif: MockNotification) {
    const params = new URLSearchParams({
      lease: notif.leaseCode,
      period: notif.period,
      periodLabel: notif.periodLabel,
      mall: notif.mallName,
      notificationId: notif.id,
    });
    router.push(`/sales-data-chat?${params.toString()}`);
  }

  function handleLogout() {
    logout();
    router.push("/login");
  }

  const isAdmin = user.role === "SALES_DATA_APPROVER";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <header className="bg-[#1A1A2E] text-white shadow-md">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-[#C9A84C] rounded-lg flex items-center justify-center text-sm font-bold">C</div>
            <div>
              <p className="text-sm font-bold">Cenomi Tenant Central</p>
              <p className="text-[10px] text-white/50">Mall Management Platform</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            {/* Notification bell */}
            {user.notifications.length > 0 && (
              <div className="relative">
                <button className="w-8 h-8 flex items-center justify-center rounded-full bg-white/10 hover:bg-white/20 transition">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                </button>
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-[9px] font-bold rounded-full flex items-center justify-center">
                  {user.notifications.length}
                </span>
              </div>
            )}
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 bg-[#C9A84C] rounded-full flex items-center justify-center text-xs font-bold">
                {user.label.charAt(0)}
              </div>
              <span className="text-xs text-white/70">{user.label}</span>
            </div>
            <button
              onClick={handleLogout}
              className="text-xs text-white/50 hover:text-white/80 transition"
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
          <nav className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
            {[
              { icon: "🏠", label: "Dashboard", active: true },
              { icon: "📊", label: "Sales Data", active: false },
              { icon: "📋", label: "Service Requests", active: false },
              { icon: "🏢", label: "Leasing & Deals", active: false },
              { icon: "💰", label: "Finance", active: false },
            ].map((item) => (
              <button
                key={item.label}
                className={`w-full flex items-center gap-2.5 px-4 py-3 text-sm text-left transition-colors ${
                  item.active
                    ? "bg-[#FFF9EE] text-[#C9A84C] font-semibold border-r-2 border-[#C9A84C]"
                    : "text-gray-600 hover:bg-gray-50"
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
          <div className="bg-gradient-to-r from-[#1A1A2E] to-[#2D2D4E] rounded-xl p-5 text-white flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold">Welcome, {user.label} 👋</h1>
              <p className="text-sm text-white/60 mt-1">
                {isAdmin
                  ? "Review and approve sales data submissions from tenants."
                  : "Submit your monthly sales data and track submission status."}
              </p>
            </div>
            <button
              onClick={() => router.push("/sales-data-chat")}
              className="bg-[#C9A84C] hover:bg-[#B8973B] text-white text-sm font-semibold px-4 py-2 rounded-lg transition shadow"
            >
              {isAdmin ? "Review Submissions →" : "Open AI Assistant →"}
            </button>
          </div>

          {/* Notifications section — tenant only */}
          {!isAdmin && user.notifications.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                  <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
                  Action Required — Sales Data Pending
                </h2>
                <span className="text-xs text-gray-400">{user.notifications.length} notification{user.notifications.length !== 1 ? "s" : ""}</span>
              </div>

              <div className="space-y-3">
                {user.notifications.map((notif) => {
                  const colors = STATUS_COLORS[notif.status] ?? STATUS_COLORS.pending;
                  return (
                    <div
                      key={notif.id}
                      className={`${colors.bg} border border-gray-200 rounded-xl p-4 flex items-start justify-between gap-4 hover:shadow-md transition-shadow cursor-pointer`}
                      onClick={() => handleNotificationClick(notif)}
                    >
                      <div className="flex items-start gap-3">
                        <div className={`w-2.5 h-2.5 ${colors.dot} rounded-full mt-1.5 flex-shrink-0`} />
                        <div>
                          <p className="text-sm font-semibold text-gray-800">{notif.title}</p>
                          <p className="text-xs text-gray-600 mt-0.5">{notif.body}</p>
                          <div className="flex items-center gap-3 mt-2">
                            <span className="text-xs font-mono bg-white border border-gray-200 px-2 py-0.5 rounded text-gray-700">
                              {notif.leaseCode}
                            </span>
                            <span className="text-xs text-gray-500">{notif.mallName}</span>
                            <span className="text-xs text-gray-400">{timeAgo(notif.createdAt)}</span>
                          </div>
                        </div>
                      </div>

                      <button
                        onClick={(e) => { e.stopPropagation(); handleNotificationClick(notif); }}
                        className="flex-shrink-0 bg-[#C9A84C] hover:bg-[#B8973B] text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition whitespace-nowrap shadow-sm"
                      >
                        Submit Now →
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* Admin quick-actions */}
          {isAdmin && (
            <section>
              <h2 className="text-sm font-bold text-gray-800 mb-3">Quick Actions</h2>
              <div className="grid grid-cols-2 gap-4">
                {[
                  { icon: "📋", label: "Review Pending Submissions", desc: "2 submissions awaiting review", action: () => router.push("/sales-data-chat") },
                  { icon: "✅", label: "Approve Sales Data", desc: "Bulk approve validated data", action: () => router.push("/sales-data-chat") },
                ].map((card) => (
                  <button
                    key={card.label}
                    onClick={card.action}
                    className="text-left bg-white rounded-xl border border-gray-100 shadow-sm p-4 hover:border-[#C9A84C]/40 hover:shadow-md transition-all"
                  >
                    <span className="text-2xl mb-2 block">{card.icon}</span>
                    <p className="text-sm font-semibold text-gray-800">{card.label}</p>
                    <p className="text-xs text-gray-400 mt-1">{card.desc}</p>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Status summary cards */}
          <section>
            <h2 className="text-sm font-bold text-gray-800 mb-3">Sales Data Overview</h2>
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Pending", count: isAdmin ? 3 : 2, color: "text-yellow-600", bg: "bg-yellow-50", border: "border-yellow-100" },
                { label: "Submitted", count: isAdmin ? 2 : 0, color: "text-blue-600", bg: "bg-blue-50", border: "border-blue-100" },
                { label: "Approved", count: isAdmin ? 5 : 0, color: "text-green-600", bg: "bg-green-50", border: "border-green-100" },
              ].map((stat) => (
                <div key={stat.label} className={`${stat.bg} border ${stat.border} rounded-xl p-4 text-center`}>
                  <p className={`text-2xl font-bold ${stat.color}`}>{stat.count}</p>
                  <p className="text-xs text-gray-500 mt-1">{stat.label}</p>
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
