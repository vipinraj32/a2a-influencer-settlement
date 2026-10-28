// Browser wallet (MetaMask or any EIP-1193 provider). The private key never leaves the wallet:
// the server builds the exact message, the wallet shows it to the user and signs it.

import type { ChainInfo, TypedData } from "./types";

interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on?(event: string, handler: (...args: unknown[]) => void): void;
  removeListener?(event: string, handler: (...args: unknown[]) => void): void;
}

declare global {
  interface Window {
    ethereum?: Eip1193Provider;
  }
}

export class WalletError extends Error {}

function provider(): Eip1193Provider {
  if (!window.ethereum) throw new WalletError("No browser wallet found. Install MetaMask (metamask.io) and reload the page.");
  return window.ethereum;
}

function explain(e: unknown): never {
  const err = e as { code?: number; message?: string };
  if (err?.code === 4001) throw new WalletError("You rejected the request in your wallet.");
  if (err?.code === -32002) throw new WalletError("Your wallet already has a pending request. Open MetaMask to finish it.");
  throw new WalletError(err?.message ?? String(e));
}

export const hasWallet = () => typeof window !== "undefined" && !!window.ethereum;

export async function connect(): Promise<string> {
  try {
    const accounts = (await provider().request({ method: "eth_requestAccounts" })) as string[];
    if (!accounts.length) throw new WalletError("The wallet returned no account.");
    return accounts[0];
  } catch (e) {
    if (e instanceof WalletError) throw e;
    explain(e);
  }
}

export async function currentAccount(): Promise<string | null> {
  if (!window.ethereum) return null;
  const accounts = (await window.ethereum.request({ method: "eth_accounts" })) as string[];
  return accounts[0] ?? null;
}

/** Switches the wallet to HashKey Chain Testnet, adding the network first if needed (EIP-712 domains are chain-bound). */
export async function ensureChain(chain: ChainInfo): Promise<void> {
  const chainId = "0x" + chain.id.toString(16);
  const p = provider();
  try {
    await p.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  } catch (e) {
    if ((e as { code?: number }).code !== 4902) explain(e);
    try {
      await p.request({
        method: "wallet_addEthereumChain",
        params: [
          {
            chainId,
            chainName: chain.name,
            nativeCurrency: { name: chain.currency, symbol: chain.currency, decimals: 18 },
            rpcUrls: [chain.rpc_url],
            blockExplorerUrls: [chain.explorer_url],
          },
        ],
      });
    } catch (e2) {
      explain(e2);
    }
  }
}

function utf8Hex(text: string): string {
  return "0x" + Array.from(new TextEncoder().encode(text), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** EIP-191 personal_sign — wallet linking and the influencer's agent authorization. */
export async function personalSign(address: string, message: string): Promise<string> {
  try {
    return (await provider().request({ method: "personal_sign", params: [utf8Hex(message), address] })) as string;
  } catch (e) {
    explain(e);
  }
}

/** EIP-712 — the Payment Mandate (EIP-3009 TransferWithAuthorization) for one Cart Mandate. */
export async function signTypedData(address: string, typedData: TypedData): Promise<string> {
  try {
    return (await provider().request({ method: "eth_signTypedData_v4", params: [address, JSON.stringify(typedData)] })) as string;
  } catch (e) {
    explain(e);
  }
}

export function onAccountsChanged(handler: (account: string | null) => void): () => void {
  const eth = window.ethereum;
  if (!eth?.on) return () => undefined;
  const listener = (...args: unknown[]) => handler(((args[0] as string[]) ?? [])[0] ?? null);
  eth.on("accountsChanged", listener);
  return () => eth.removeListener?.("accountsChanged", listener);
}

export const sameAddress = (a?: string | null, b?: string | null) => !!a && !!b && a.toLowerCase() === b.toLowerCase();
