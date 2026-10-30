/** ZyroXQ brand mark: two agent nodes joined by a Z-shaped path (the negotiated agreement between them). */
export function LogoMark({ size = 28, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <defs>
        <linearGradient id="zx-mark" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4F46E5" />
          <stop offset="1" stopColor="#0EA5A4" />
        </linearGradient>
      </defs>
      <rect width="40" height="40" rx="11" fill="url(#zx-mark)" />
      <path d="M12 12.5h13.5L14.5 27.5H28" fill="none" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="12.5" r="3" fill="#fff" />
      <circle cx="28" cy="27.5" r="3" fill="#fff" />
    </svg>
  );
}

/** Mark + wordmark. "XQ" carries the accent so the name reads as Zyro·XQ. */
export function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`zx-logo ${className}`}>
      <LogoMark size={size} />
      <span className="zx-word" style={{ fontSize: size * 0.72 }}>
        Zyro<span className="zx-word-accent">XQ</span>
      </span>
    </span>
  );
}
