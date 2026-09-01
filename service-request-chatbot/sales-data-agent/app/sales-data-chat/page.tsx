import { Suspense } from "react";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { SalesDataSessionProvider } from "@/lib/context/SalesDataSessionContext";
import { SalesDataChat } from "@/components/chatbot/SalesDataChat";
import { SalesDataLeftPanel } from "@/components/SalesDataLeftPanel";

function SplitScreenContent() {
  return (
    <div className="flex h-screen gap-3 p-3 overflow-hidden">
      {/* ── Left panel — platform mirror ── */}
      <div className="flex-1 min-w-0 overflow-hidden">
        <SalesDataLeftPanel />
      </div>

      {/* Divider */}
      <div className="w-px bg-gradient-to-b from-transparent via-[#C9A84C]/40 to-transparent flex-shrink-0 self-stretch" />

      {/* ── Right panel — AI chatbot ── */}
      <div className="w-[420px] flex-shrink-0 flex flex-col glass-strong rounded-2xl overflow-hidden">
        <Suspense>
          <SalesDataChat />
        </Suspense>
      </div>
    </div>
  );
}

export default function SalesDataChatPage() {
  return (
    <AuthGuard>
      <SalesDataSessionProvider>
        <SplitScreenContent />
      </SalesDataSessionProvider>
    </AuthGuard>
  );
}
