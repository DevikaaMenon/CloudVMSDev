import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { get, post, setToken, getToken, setUnauthorizedHandler } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  can: (perm: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await get<User>("/auth/me")); } catch { setToken(null); setUser(null); }
    setLoading(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => { setToken(null); setUser(null); });
    refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const r = await post<{ access_token: string; user: User }>("/auth/login", { username, password });
    setToken(r.access_token);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    try { await post("/auth/logout"); } catch { /* token may already be invalid */ }
    setToken(null);
    setUser(null);
  }, []);

  const can = useCallback((perm: string) => !!user?.permissions.includes(perm), [user]);
  const value = useMemo(() => ({ user, loading, login, logout, refresh, can }), [user, loading, login, logout, refresh, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
