import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import { closeStream } from "./live";
import type { Me, PlatformStatus } from "./types";

interface Session {
  me: Me | null; // null = signed out
  status: PlatformStatus | null;
  loading: boolean;
  setMe: (me: Me) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const [meResult, statusResult] = await Promise.allSettled([api.me(), api.status()]);
    if (meResult.status === "fulfilled") setMe(meResult.value);
    else if (meResult.reason instanceof ApiError && meResult.reason.status === 401) setMe(null);
    if (statusResult.status === "fulfilled") setStatus(statusResult.value);
    setLoading(false);
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    closeStream();
    setMe(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return <SessionContext.Provider value={{ me, status, loading, setMe, refresh, signOut }}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

/** For pages behind sign-in: the session is guaranteed to have a user. */
export function useMe(): Me {
  const { me } = useSession();
  if (!me) throw new Error("useMe on a signed-out page");
  return me;
}
