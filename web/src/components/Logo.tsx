/** ZyroXQ brand assets, served from web/public. */
export const LOGO = {
  mark: "/ZyroXQ_Monogram_Blue.svg",
  /** Blue monogram + ink wordmark, for light backgrounds. */
  onLight: encodeURI("/ZyroXQ — Black logo.svg"),
  /** Blue monogram + white wordmark, for dark backgrounds. */
  onDark: encodeURI("/ZyroXQ — White logo.svg"),
};

// The wordmark files are 573×120 but the artwork ends at x≈392, so the frame crops the empty right side.
const ART_W = 392;
const FILE_W = 573;
const FILE_H = 120;

export function LogoMark({ size = 28, title }: { size?: number; title?: string }) {
  return <img className="zx-mark" src={LOGO.mark} width={size} height={size} alt={title ?? ""} aria-hidden={title ? undefined : true} />;
}

/**
 * Full wordmark. `tone="auto"` follows the colour scheme (ink text on light, white text on dark);
 * "light"/"dark" pin it for surfaces that never change.
 */
export function Logo({ size = 28, tone = "auto", className = "" }: { size?: number; tone?: "auto" | "light" | "dark"; className?: string }) {
  const h = Math.round(size * 1.15);
  const src = tone === "dark" ? LOGO.onDark : LOGO.onLight;
  return (
    <span className={`zx-logo ${className}`} style={{ width: Math.round((h * ART_W) / FILE_H), height: h }}>
      <picture>
        {tone === "auto" && <source srcSet={LOGO.onDark} media="(prefers-color-scheme: dark)" />}
        <img src={src} width={Math.round((h * FILE_W) / FILE_H)} height={h} alt="ZyroXQ" />
      </picture>
    </span>
  );
}
