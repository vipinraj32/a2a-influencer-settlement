import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { Logo } from "./Logo";
import { useStreamConnected } from "../live";
import { Icon } from "../pages/landing/parts";
import { useMe, useSession } from "../session";
import { Address, Avatar } from "./ui";

const NAV = {
  advertiser: [
    { to: "/advertiser", label: "Dashboard", icon: "home", end: true },
    { to: "/advertiser/campaigns", label: "Campaigns", icon: "ledger" },
    { to: "/advertiser/new", label: "New campaign", icon: "plus" },
    { to: "/advertiser/creators", label: "Creators", icon: "users" },
    { to: "/advertiser/agent", label: "My agent", icon: "bot" },
  ],
  influencer: [
    { to: "/influencer", label: "Overview", icon: "home", end: true },
    { to: "/influencer/deals", label: "My deals", icon: "ledger" },
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
  useEffect(() => {
    setOpen(false);
  }, [pathname]);
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
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
