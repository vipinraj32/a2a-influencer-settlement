import { Link, Navigate, useSearchParams } from "react-router";
import { Logo } from "../components/Logo";
import { api } from "../api";
import { Notice } from "../components/ui";
import { useSession } from "../session";

export function Login() {
  const { me, status } = useSession();
  const [params] = useSearchParams();
  const error = params.get("error");
  if (me) return <Navigate to="/app" replace />;

  return (
    <div className="login">
      <div className="login-hero">
        <Link to="/" className="brand big" aria-label="ZyroXQ home">
          <Logo size={36} />
        </Link>
        <h1>Influencer deals your agents negotiate, and escrow pays only on proof.</h1>
        <p className="lead">
          Advertiser agents find creator agents over A2A and agree terms. Your own wallet signs the HSP Payment Mandate, the money waits in escrow,
          and the creator is paid once their YouTube post is verified.
        </p>
        <ol className="how">
          <li>
            <strong>Negotiate</strong> Agents agree on a fixed fee + per-view rate within your limits
          </li>
          <li>
            <strong>Sign</strong> You sign one EIP-712 Payment Mandate per creator in MetaMask
          </li>
          <li>
            <strong>Verify</strong> The YouTube post is checked for channel, #ad disclosure and prompt injection
          </li>
          <li>
            <strong>Settle</strong> Fixed fee after 48 h live, performance fee at day 7, the rest is refunded
          </li>
        </ol>
      </div>

      <div className="login-card card">
        <h2>Sign in</h2>
        {error && <Notice tone="bad">{error}</Notice>}
        {status && !status.login_configured && (
          <Notice tone="warn">Google sign-in is not configured on the server yet. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.</Notice>
        )}
        <a className="btn btn-primary btn-block" href={api.loginUrl("advertiser")}>
          <GoogleMark /> Continue as an advertiser
        </a>
        <p className="muted small">Run campaigns and sign payments from your wallet.</p>
        <a className="btn btn-secondary btn-block" href={api.loginUrl("influencer")}>
          <GoogleMark /> Continue as an influencer
        </a>
        <p className="muted small">
          You'll also be asked for read-only YouTube access. It proves you own your channel, so nobody else can claim your posts.
        </p>
        <p className="muted small">You need a browser wallet (MetaMask) for the next step.</p>
      </div>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
