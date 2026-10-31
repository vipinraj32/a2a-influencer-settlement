import type { ReactNode } from "react";
import { Link } from "react-router";

/** Honest implementation status, shown next to every capability. */
export type Status = "built" | "beta" | "integrating" | "planned";

export const STATUS_LABEL: Record<Status, string> = {
  built: "Built",
  beta: "Beta",
  integrating: "In integration",
  planned: "Planned",
};

export function StatusTag({ status, note }: { status: Status; note?: string }) {
  return (
    <span className={`lp-status lp-status-${status}`} title={note}>
      <span className="lp-status-dot" aria-hidden />
      {STATUS_LABEL[status]}
      {note && <span className="lp-sr"> — {note}</span>}
    </span>
  );
}

type CtaProps = { to: string; children: ReactNode; variant?: "primary" | "outline" | "dark"; size?: "md" | "lg"; external?: boolean };

/** Internal routes use the router, anchors and server routes (OAuth) use plain links. */
export function Cta({ to, children, variant = "primary", size = "md", external }: CtaProps) {
  const cls = `lp-btn lp-btn-${variant} lp-btn-${size}`;
  if (external || to.startsWith("#") || to.startsWith("http") || to.startsWith("/auth/")) {
    return (
      <a className={cls} href={to} {...(to.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>
        {children}
      </a>
    );
  }
  return (
    <Link className={cls} to={to}>
      {children}
    </Link>
  );
}

const PATHS: Record<string, ReactNode> = {
  check: <path d="M5 12.5l4.2 4.2L19 7" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  arrowLeft: <path d="M19 12H5M11 6l-6 6 6 6" />,
  arrowUp: <path d="M12 19V5M6 11l6-6 6 6" />,
  agents: (
    <>
      <circle cx="6" cy="7" r="3" />
      <circle cx="18" cy="17" r="3" />
      <path d="M9 7h6l-6 10h6" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l7 3v6c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  ledger: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4.5-4.5" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="15" r="4" />
      <path d="M11 12l9-9M17 6l3 3M15 8l2 2" />
    </>
  ),
  chart: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  alert: (
    <>
      <path d="M12 3l10 18H2z" />
      <path d="M12 10v5M12 18h.01" />
    </>
  ),
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  github: (
    <path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21" />
  ),
};

export function Icon({ name, size = 20 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="lp-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  );
}
