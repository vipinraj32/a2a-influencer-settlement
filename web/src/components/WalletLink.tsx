import { useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import * as wallet from "../wallet";
import { Address, ErrorText, Notice } from "./ui";

/** Connect the browser wallet and prove ownership with one signature. For influencers the same
 * signature authorizes their agent key to sign deal terms. */
export function WalletLink({ onLinked }: { onLinked?: () => void }) {
  const { me, setMe, status } = useSession();
  const [message, setMessage] = useState<string | null>(null);
  const [step, setStep] = useState<"idle" | "connecting" | "signing">("idle");
  const [error, setError] = useState<string | null>(null);
  if (!me) return null;
  const user = me.user;
  const influencer = user.role === "influencer";

  async function link() {
    setError(null);
    try {
      setStep("connecting");
      const address = await wallet.connect();
      if (status) await wallet.ensureChain(status.chain);
      const challenge = await api.walletChallenge(address);
      setMessage(challenge.message);
      setStep("signing");
      const signature = await wallet.personalSign(address, challenge.message);
      setMe(await api.linkWallet(signature));
      setMessage(null);
      onLinked?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStep("idle");
    }
  }

  return (
    <div className="wallet-link">
      {user.wallet && (
        <p>
          Linked wallet: <Address value={user.wallet} /> {influencer && user.delegation_signature && <span className="chip good">agent authorized</span>}
        </p>
      )}
      {!wallet.hasWallet() && (
        <Notice tone="warn">
          No browser wallet detected. Install <a href="https://metamask.io/download/" target="_blank" rel="noreferrer">MetaMask</a>, then reload.
        </Notice>
      )}
      <p className="muted small">
        {influencer
          ? "Payouts go to this wallet. Your signature also authorizes your ZyroXQ agent key (" +
            (user.agent_signer ? `${user.agent_signer.slice(0, 8)}…` : "pending") +
            ") to sign deal terms for your channel. Your wallet's private key never leaves your browser."
          : "This wallet signs every Payment Mandate that funds the escrow. Its private key never leaves your browser."}
        {" "}The wallet is switched to {status?.chain.name ?? "HashKey Chain Testnet"} (chain {status?.chain.id ?? 133}).
      </p>
      <button type="button" className="btn btn-primary" onClick={link} disabled={step !== "idle" || !wallet.hasWallet()}>
        {step === "connecting" ? "Connecting…" : step === "signing" ? "Sign the message in your wallet…" : user.wallet ? "Re-link wallet" : "Connect wallet"}
      </button>
      {message && step === "signing" && (
        <pre className="sign-preview" aria-label="Message you are signing">
          {message}
        </pre>
      )}
      <ErrorText error={error} />
    </div>
  );
}
