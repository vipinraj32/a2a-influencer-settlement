import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { Logo } from "./Logo";
import { api } from "../api";
import { useStreamConnected } from "../live";
import { Icon } from "../pages/landing/parts";
import { useMe, useSession } from "../session";
import { Address, Avatar } from "./ui";

function PlatformBar() {
  const { status, refresh } = useSession();
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (!status) return null;

  async function advance(hours: number) {
    setMsg(null);
    try {
      const r = await api.advance(hours);
      setMsg(`Clock +${r.clock_offset_hours.toFixed(0)} h · ${r.actions} escrow action(s)`);
      await refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="platform-bar">
      <div className="pills">
        <span className={`pill ${status.settlement_mode === "hosted" ? "good" : "warn"}`}>HSP: {status.settlement_mode}</span>
        <span className={`pill ${status.escrow.simulated ? "warn" : "good"}`}>Escrow: {status.escrow.simulated ? "simulated" : "on-chain"}</span>
        <span className={`pill ${status.verifier_online ? "good" : "bad"}`}>Verifier: {status.verifier_online ? "YouTube API" : "offline"}</span>
        <span className={`pill ${status.llm ? "good" : "warn"}`}>Agents: {status.llm ?? "rules only"}</span>
        <span className={`pill ${status.store === "PostgresStore" ? "good" : "warn"}`}>{status.store === "PostgresStore" ? "PostgreSQL" : "In-memory store"}</span>
        {status.warnings.length > 0 && (
          <button type="button" className="link" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"} {status.warnings.length} setup note{status.warnings.length > 1 ? "s" : ""}
          </button>
        )}
      </div>
      {status.escrow.simulated && (
        <div className="time-travel" title="Simulated escrow only: moves the platform clock to trigger releases and refunds">
          <span className="muted small">Dev clock {status.clock_offset_hours ? `+${status.clock_offset_hours.toFixed(0)} h` : "now"}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => advance(49)}>
            +49 h
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => advance(24 * 7)}>
            +7 days
          </button>
          {msg && <span className="muted small">{msg}</span>}
        </div>
      )}
      {open && (
        <ul className="warnings">
          {status.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const NAV = {
  advertiser: [
    { to: "/advertiser", label: "Dashboard", icon: "home", end: true },
    { to: "/advertiser/new", label: "New campaign", icon: "plus" },
    { to: "/advertiser/creators", label: "Creators", icon: "users" },
    { to: "/advertiser/agent", label: "My agent", icon: "bot" },
  ],
  influencer: [
    { to: "/influencer", label: "My deals", icon: "home", end: true },
    { to: "/influencer/agent", label: "My agent", icon: "bot" },
    { to: "/influencer/profile", label: "My profile", icon: "users" },
  ],
};

export function Layout() {
  const { user } = useMe();
  const { signOut } = useSession();
  const live = useStreamConnected();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const home = user.role === "advertiser" ? "/advertiser" : "/influencer";

  // close the mobile drawer whenever the page changes
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={`app ${open ? "app-open" : ""}`}>
      <aside className="sidebar" id="app-sidebar">
        <NavLink to={home} className="sidebar-brand" aria-label="ZyroXQ dashboard">
          <Logo size={30} />
        </NavLink>
        <nav className="sidebar-nav" aria-label="Main">
          <p className="sidebar-label">{user.role === "advertiser" ? "Advertiser" : "Creator"}</p>
          {NAV[user.role].map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <Icon name={n.icon} size={20} />
              {n.label}
            </NavLink>
          ))}
          <p className="sidebar-label">Settings</p>
          <NavLink to="/account">
            <Icon name="user" size={20} />
            Account
          </NavLink>
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-live">
            <span className={`live-dot ${live ? "on" : "off"}`} />
            {live ? "Live updates on" : "Reconnecting…"}
          </div>
          <div className="sidebar-user">
            <Avatar src={user.picture} name={user.name} size={36} />
            <div>
              <strong>{user.name}</strong>
              {user.wallet ? <Address value={user.wallet} /> : <span className="muted small">No wallet linked</span>}
            </div>
          </div>
          <button
            type="button"
            className="sidebar-signout"
            onClick={async () => {
              await signOut();
              navigate("/");
            }}
          >
            <Icon name="logout" size={18} />
            Sign out
          </button>
        </div>
      </aside>
      <button type="button" className="sidebar-scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setOpen(false)} />

      <div className="app-main">
        <header className="mobilebar">
          <button type="button" className="mobilebar-btn" aria-expanded={open} aria-controls="app-sidebar" onClick={() => setOpen(!open)}>
            <Icon name={open ? "close" : "menu"} size={22} />
            <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
          </button>
          <NavLink to={home} aria-label="ZyroXQ dashboard">
            <Logo size={26} />
          </NavLink>
          <Avatar src={user.picture} name={user.name} size={30} />
        </header>
        <PlatformBar />
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
