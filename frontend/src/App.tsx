import { useEffect, useRef, useState } from "react";
import { logout as apiLogout, refreshFromCookie, refreshSession } from "./api";
import ChatWindow from "./components/ChatWindow";
import Login from "./components/Login";

interface Auth {
  token: string;
  role: string;
  username: string;
}

const STORAGE_KEY = "nha_auth";
// Tokens last 8 hours; renew well inside that so a long analyst session never
// gets cut off mid-work.
const RENEW_EVERY_MS = 60 * 60 * 1000;

function readStoredAuth(): Auth | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Auth) : null;
  } catch {
    return null; // storage blocked or holding junk
  }
}

export default function App() {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [restoring, setRestoring] = useState(true);
  // True when the httpOnly auth cookie works, in which case we deliberately
  // keep NO token in browser storage — an injected script then has nothing to
  // read. False on cross-origin deployments (separate API host, or the GitHub
  // Pages demo), where the cookie would be a third-party cookie and is blocked
  // by default in several browsers; there we fall back to session storage.
  const cookieMode = useRef(false);

  // Restore a session on load: stored token first (it means cookie auth was
  // unavailable last time), otherwise ask the backend to renew from the cookie.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = readStoredAuth();
      if (stored) {
        if (!cancelled) {
          setAuth(stored);
          setRestoring(false);
        }
        return;
      }
      const renewed = await refreshFromCookie();
      if (cancelled) return;
      if (renewed) {
        cookieMode.current = true;
        setAuth({
          token: renewed.access_token,
          role: renewed.role,
          username: renewed.username,
        });
      }
      setRestoring(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the session alive while the tab is open.
  useEffect(() => {
    if (!auth) return;
    const id = window.setInterval(async () => {
      const renewed = await refreshSession(auth.token);
      if (!renewed) return; // expired or offline; the next 401 surfaces it
      const next = {
        token: renewed.access_token,
        role: renewed.role,
        username: renewed.username,
      };
      setAuth(next);
      if (!cookieMode.current) persist(next);
    }, RENEW_EVERY_MS);
    return () => window.clearInterval(id);
  }, [auth]);

  function persist(a: Auth) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(a));
    } catch {
      // Private mode or blocked storage: the in-memory session still works
      // until the tab is reloaded.
    }
  }

  async function handleLogin(token: string, role: string, username: string) {
    const a = { token, role, username };
    setAuth(a);
    // Did login set a usable cookie? Probe by renewing with the cookie alone.
    // If that works we never write the token to storage.
    const viaCookie = await refreshFromCookie();
    cookieMode.current = viaCookie !== null;
    if (!cookieMode.current) persist(a);
  }

  async function handleLogout() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* nothing to remove */
    }
    setAuth(null);
    cookieMode.current = false;
    await apiLogout(); // clears the httpOnly cookie server-side
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        {restoring ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-faint">
            Loading…
          </div>
        ) : auth ? (
          <ChatWindow
            token={auth.token}
            role={auth.role}
            username={auth.username}
            onLogout={handleLogout}
          />
        ) : (
          <Login onLogin={handleLogin} />
        )}
      </div>
      <footer className="border-t border-line bg-surface px-4 py-1.5 text-center text-[11px] text-ink-faint">
        For support, contact the developer at{" "}
        <a href="mailto:mchaurasiya@wjcf.in" className="font-medium text-brand hover:underline">
          mchaurasiya@wjcf.in
        </a>
      </footer>
    </div>
  );
}
