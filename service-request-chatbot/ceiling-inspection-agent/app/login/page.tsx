"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DEMO_ROLES, loginWithRole, type DemoRole } from "@/lib/auth";

const ORDER: DemoRole[] = ["TENANT", "DD_ENGINEER", "FM_MANAGER"];

const STEPS: Record<DemoRole, string> = {
  TENANT: "Steps 1 & 4",
  DD_ENGINEER: "Step 2",
  FM_MANAGER: "Step 3",
};

export default function LoginPage() {
  const router = useRouter();
  const [pending, setPending] = useState<DemoRole | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function signIn(role: DemoRole) {
    setPending(role);
    setError(null);
    try {
      await loginWithRole(role);
      router.push("/inspections");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPending(null);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <div className="glass-strong rounded-3xl p-8 w-full max-w-3xl">
        <h1 className="text-xl font-semibold">Ceiling Inspection Agent — demo</h1>
        <p className="text-sm text-white/60 mt-1 mb-6">
          Open and Close Ceiling Inspection, end to end: raise → RDD Project Manager (level 1) →
          FM Manager (level 2) → tenant acknowledgement. Sign in as each actor in turn.
        </p>
        <div className="grid sm:grid-cols-2 gap-3">
          {ORDER.map((role) => {
            const info = DEMO_ROLES[role];
            return (
              <button
                key={role}
                className="glass rounded-2xl p-4 text-left hover:bg-white/15 disabled:opacity-50"
                disabled={pending !== null}
                onClick={() => void signIn(role)}
              >
                <div className="text-xs text-[#E4C063]">
                  {STEPS[role]} · {info.step}
                </div>
                <div className="font-semibold mt-1">
                  {info.label} <span className="text-white/50 font-normal">— {info.person}</span>
                </div>
                <div className="text-xs text-white/60 mt-1">{info.description}</div>
                <div className="text-[11px] text-white/40 mt-2">{info.username}</div>
                {pending === role && <div className="text-xs mt-2">Signing in…</div>}
              </button>
            );
          })}
        </div>
        {error && (
          <div className="mt-4 text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
            {error}
          </div>
        )}
      </div>
    </main>
  );
}
