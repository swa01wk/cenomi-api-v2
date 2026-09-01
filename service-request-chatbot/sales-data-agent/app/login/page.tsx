"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { loginWithRole, DEMO_ROLES } from "@/lib/api/auth-client";
import type { DemoRole } from "@/lib/api/auth-client";

export default function LoginPage() {
  const router = useRouter();
  const [selectedRole, setSelectedRole] = useState<DemoRole>("TENANT");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      await loginWithRole(selectedRole);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="glass-strong rounded-3xl w-full max-w-md overflow-hidden">
        <div className="px-8 pt-8 pb-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-sm font-bold text-[#1A1A2E] shadow-[0_0_24px_rgba(201,168,76,0.45)]">
              C
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-white">Sales Data Agent</h1>
              <p className="text-xs text-white/50">Cenomi Mall Platform</p>
            </div>
          </div>
          <p className="text-sm text-white/60">
            AI-powered sales data submission — select a demo role to continue.
          </p>
        </div>
        <div className="gold-line" />

        <form onSubmit={handleLogin} className="p-8 space-y-6">
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-white/40 mb-3">
              Demo role
            </h2>
            <div className="space-y-2.5">
              {(Object.entries(DEMO_ROLES) as [DemoRole, typeof DEMO_ROLES[DemoRole]][]).map(
                ([roleKey, roleInfo]) => (
                  <label
                    key={roleKey}
                    className={`flex items-start gap-3 p-4 rounded-2xl cursor-pointer transition-all ${
                      selectedRole === roleKey
                        ? "bg-[#C9A84C]/15 border border-[#C9A84C]/50 shadow-[0_0_20px_rgba(201,168,76,0.12)]"
                        : "bg-white/5 border border-white/10 hover:border-white/20"
                    }`}
                  >
                    <input
                      type="radio"
                      name="role"
                      value={roleKey}
                      checked={selectedRole === roleKey}
                      onChange={() => setSelectedRole(roleKey)}
                      className="mt-0.5 accent-[#C9A84C]"
                    />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{roleInfo.icon}</span>
                        <span className="font-medium text-white">{roleInfo.label}</span>
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${
                            roleInfo.type === "tenant"
                              ? "bg-sky-400/15 text-sky-200"
                              : "bg-violet-400/15 text-violet-200"
                          }`}
                        >
                          {roleInfo.type}
                        </span>
                      </div>
                      <p className="text-xs text-white/45 mt-1 leading-relaxed">
                        {roleInfo.description}
                      </p>
                    </div>
                  </label>
                )
              )}
            </div>
          </div>

          {error && (
            <div className="bg-red-500/15 border border-red-400/30 text-red-200 text-sm rounded-xl p-3">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-[#1A1A2E] font-semibold py-3 px-6 rounded-xl hover:opacity-90 disabled:opacity-60 transition-all shadow-[0_8px_24px_rgba(201,168,76,0.28)]"
          >
            {isLoading ? "Signing in…" : "Enter demo"}
          </button>

          <p className="text-xs text-center text-white/30">
            Demo environment with mock sales data
          </p>
        </form>
      </div>
    </div>
  );
}
