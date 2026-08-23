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
    <div className="min-h-screen bg-gradient-to-br from-[#1A1A2E] to-[#16213E] flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-r from-[#C9A84C] to-[#E4C063] p-8 text-white">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 bg-white/20 rounded-full flex items-center justify-center text-xl">
              📊
            </div>
            <div>
              <h1 className="text-xl font-bold">Sales Data Agent</h1>
              <p className="text-sm text-white/80">Cenomi Mall Platform</p>
            </div>
          </div>
          <p className="text-sm text-white/70 mt-3">
            AI-powered sales data submission assistant
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleLogin} className="p-8 space-y-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-800 mb-4">
              Select Demo Role
            </h2>
            <div className="space-y-3">
              {(Object.entries(DEMO_ROLES) as [DemoRole, typeof DEMO_ROLES[DemoRole]][]).map(
                ([roleKey, roleInfo]) => (
                  <label
                    key={roleKey}
                    className={`flex items-start gap-3 p-4 rounded-xl border-2 cursor-pointer transition-all ${
                      selectedRole === roleKey
                        ? "border-[#C9A84C] bg-[#FFF9EE]"
                        : "border-gray-200 hover:border-gray-300"
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
                        <span className="font-medium text-gray-900">
                          {roleInfo.label}
                        </span>
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            roleInfo.type === "tenant"
                              ? "bg-blue-100 text-blue-700"
                              : "bg-purple-100 text-purple-700"
                          }`}
                        >
                          {roleInfo.type}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mt-1">
                        {roleInfo.description}
                      </p>
                    </div>
                  </label>
                )
              )}
            </div>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white font-semibold py-3 px-6 rounded-xl hover:opacity-90 disabled:opacity-60 transition-all shadow-md"
          >
            {isLoading ? "Signing in…" : "Enter Demo →"}
          </button>

          <p className="text-xs text-center text-gray-400">
            This is a demo environment with mock data.
          </p>
        </form>
      </div>
    </div>
  );
}
