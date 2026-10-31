import { useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router";
import { Logo } from "../components/Logo";
import { api } from "../api";
import { Notice } from "../components/ui";
import { useSession } from "../session";
import "./login.css";

type Role = "advertiser" | "influencer";

const ROLES: { id: Role; title: string; text: string; note: string }[] = [
  { id: "advertiser", title: "Advertiser", text: "Run campaigns", note: "You’ll connect a browser wallet (MetaMask) next." },
  { id: "influencer", title: "Creator", text: "Get sponsored", note: "Google will also ask for read-only YouTube access to prove you own your channel." },
];

export function Login() {
  const { me, status } = useSession();
  const [params] = useSearchParams();
  const error = params.get("error");
  const [role, setRole] = useState<Role>(params.get("as") === "influencer" ? "influencer" : "advertiser");
  if (me) return <Navigate to="/app" replace />;
  const current = ROLES.find((r) => r.id === role)!;

  return (
    <div className="auth">
      <aside className="auth-side">
        <Link to="/" className="auth-logo" aria-label="ZyroXQ home">
          <Logo size={36} tone="dark" />
        </Link>
        <div>
          <h1>
            Your agents negotiate. <span>Your rules decide.</span>
          </h1>
          <ul>
            <li>Agents agree terms within your limits</li>
            <li>Your wallet signs — never the AI</li>
            <li>Escrow pays only for verified content</li>
          </ul>
        </div>
        <p className="auth-foot">HashKey A2A track · testnet</p>
      </aside>

      <main className="auth-main">
        <Link to="/" className="auth-back">
          ← Back to home
        </Link>
        <div className="auth-card">
          <h2>Sign in</h2>
          <p className="auth-sub">Choose how you’ll use ZyroXQ.</p>
          {error && <Notice tone="bad">{error}</Notice>}
          {status && !status.login_configured && <Notice tone="warn">Google sign-in isn’t configured on the server yet.</Notice>}

          <div className="auth-roles" role="radiogroup" aria-label="Account type">
            {ROLES.map((r) => (
              <button key={r.id} type="button" role="radio" aria-checked={role === r.id} className={role === r.id ? "on" : ""} onClick={() => setRole(r.id)}>
                <strong>{r.title}</strong>
                <span>{r.text}</span>
              </button>
            ))}
          </div>

          <a className="auth-google" href={api.loginUrl(role)}>
            <GoogleMark /> Continue with Google
          </a>
          <p className="auth-note">{current.note}</p>
        </div>
      </main>
    </div>
  );
}

/** Google's own mark, kept in Google's brand colours as their sign-in guidelines require. */
function GoogleMark() {
  return (
    <span className="auth-g" aria-hidden>
      <svg width="18" height="18" viewBox="0 0 48 48">
        <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
        <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
        <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
        <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
      </svg>
    </span>
  );
}
