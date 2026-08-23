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
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-gray-400 animate-pulse">Loading…</div>
      </div>
    );
  }

  if (!user) return null;

  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}
