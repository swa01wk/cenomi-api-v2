"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated, getStoredUser } from "@/lib/api/auth-client";
import type { CurrentUser } from "@/lib/api/auth-client";

const AuthContext = createContext<CurrentUser | null>(null);

/** Consume the authenticated user inside any child of <AuthGuard>. */
export function useCurrentUser(): CurrentUser {
  const user = useContext(AuthContext);
  if (!user) throw new Error("useCurrentUser must be used inside <AuthGuard>");
  return user;
}

interface AuthGuardProps {
  children: React.ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
      return;
    } else {
      setUser(getStoredUser());
    }
    setChecking(false);
  }, [router]);

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="glass-strong rounded-2xl px-8 py-6 text-center">
          <div className="w-8 h-8 mx-auto mb-3 border-2 border-[#C9A84C] border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-white/50">Loading…</p>
        </div>
      </div>
    );
  }

  if (!user) return null;

  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}
