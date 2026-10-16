"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthGuard, useCurrentUser } from "@/components/AuthGuard";
import { CeilingChat } from "@/components/CeilingChat";
import { CeilingForm } from "@/components/CeilingForm";
import { NotificationBell } from "@/components/NotificationBell";
import { RequestList } from "@/components/RequestList";
import { DEMO_ROLES, logout } from "@/lib/auth";
import { useCeilingSession } from "@/lib/useCeilingSession";

/** Split screen: the platform form in the centre, the assistant docked on the right. */
function Workspace() {
  const router = useRouter();
  const user = useCurrentUser();
  const role = DEMO_ROLES[user.role];
  const [refreshKey, setRefreshKey] = useState(0);
  const [listOpen, setListOpen] = useState(false);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);
  const session = useCeilingSession(refresh);

  return (
    <div className="flex flex-col h-screen p-3 gap-3 overflow-hidden">
      <header className="glass rounded-2xl px-5 py-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold">Cenomi — Ceiling Inspection Agent (POC)</div>
          <div className="text-xs text-white/50 truncate">
            {role.person} · {role.label} · {role.step}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="text-xs px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10"
            onClick={() => setListOpen((o) => !o)}
          >
            My requests
          </button>
          <NotificationBell refreshKey={refreshKey} onOpen={session.openNotification} />
          <button
            className="text-xs px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
          >
            Switch role
          </button>
        </div>
      </header>

      <div className="relative flex flex-1 gap-3 min-h-0 flex-col lg:flex-row">
        {listOpen && (
          <div className="absolute inset-y-0 left-0 z-20 w-[340px] max-w-full">
            <RequestList
              refreshKey={refreshKey}
              onOpen={(srId) => {
                setListOpen(false);
                session.openSr(srId);
              }}
            />
          </div>
        )}
        <div className="flex-1 min-w-0 min-h-0">
          <CeilingForm session={session} />
        </div>
        <div className="lg:w-[440px] h-[45vh] lg:h-auto flex-shrink-0 glass-strong rounded-2xl overflow-hidden">
          <CeilingChat session={session} />
        </div>
      </div>
    </div>
  );
}

export default function InspectionsPage() {
  return (
    <AuthGuard>
      <Workspace />
    </AuthGuard>
  );
}
