import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { api } from "../api";
import { useStreamConnected } from "../live";
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

export function Layout() {
  const { user } = useMe();
  const { signOut } = useSession();
  const live = useStreamConnected();
  const navigate = useNavigate();
  const home = user.role === "advertiser" ? "/advertiser" : "/influencer";

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to={home} className="brand">
          <span className="logo">Z</span> ZyroXQ
        </NavLink>
        <nav className="nav">
          {user.role === "advertiser" ? (
            <>
              <NavLink to="/advertiser" end>
                Campaigns
              </NavLink>
              <NavLink to="/advertiser/new">New campaign</NavLink>
              <NavLink to="/advertiser/creators">Creators</NavLink>
            </>
          ) : (
            <>
              <NavLink to="/influencer" end>
                My deals
              </NavLink>
              <NavLink to="/influencer/agent">My agent</NavLink>
            </>
          )}
          <NavLink to="/account">Account</NavLink>
        </nav>
        <div className="who">
          <span className={`live-dot ${live ? "on" : "off"}`} title={live ? "Live updates connected" : "Reconnecting to live updates…"} />
          <span className="role-tag">{user.role}</span>
          {user.wallet && <Address value={user.wallet} />}
          <Avatar src={user.picture} name={user.name} size={28} />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              await signOut();
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <PlatformBar />
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
