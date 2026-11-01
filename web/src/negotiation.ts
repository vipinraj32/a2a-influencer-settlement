// Live negotiation transcript for one deal. The stored events load over REST, then new ones arrive on the app's
// shared live stream (/api/stream) the moment an agent produces them. Every event carries a per-deal event_id: we
// keep only the next id in sequence, and a gap (dropped event, reconnect) fetches what is missing from the store.
// One shared connection serves any number of open conversations, and reading never starts or re-runs a negotiation.

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { subscribeLive } from "./live";
import type { DealState, NegotiationEvent } from "./types";

export type StreamStatus = "connecting" | "live" | "done" | "legacy" | "error";

export function useNegotiationStream(dealId: string, dealState: DealState): { events: NegotiationEvent[]; status: StreamStatus } {
  const [events, setEvents] = useState<NegotiationEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  // read when the first load comes back empty; the deal finishing must not reset an open conversation
  const negotiating = useRef(dealState === "NEGOTIATING");
  negotiating.current = dealState === "NEGOTIATING";

  useEffect(() => {
    setEvents([]);
    setStatus("connecting");
    let last = 0;
    let stopped = false;
    let fetching: Promise<void> | null = null;

    const apply = (batch: NegotiationEvent[]) => {
      const fresh = batch.filter((e) => e.event_id > last).sort((a, b) => a.event_id - b.event_id);
      const next: NegotiationEvent[] = [];
      for (const e of fresh) {
        if (e.event_id !== last + 1) break; // keep the sequence whole; the next fetch fills the gap
        next.push(e);
        last = e.event_id;
      }
      if (!next.length) return;
      setEvents((prev) => [...prev, ...next]);
      setStatus(next.some((e) => e.event_type === "negotiation_completed") ? "done" : (s) => (s === "done" ? s : "live"));
    };
    const catchUp = () => {
      fetching ??= api
        .negotiationEvents(dealId, last)
        .then((batch) => {
          if (stopped) return;
          apply(batch);
          if (last === 0) setStatus((s) => (s === "connecting" ? (negotiating.current ? "live" : "legacy") : s));
        })
        .catch(() => {
          if (!stopped && last === 0) setStatus("error");
        })
        .finally(() => {
          fetching = null;
        });
      return fetching;
    };

    const unsubscribe = subscribeLive(
      (live) => {
        if (stopped || live.type !== "negotiation" || live.deal_id !== dealId || !live.event) return;
        if (live.event.event_id === last + 1) apply([live.event]);
        else if (live.event.event_id > last + 1) void catchUp();
      },
      () => void catchUp(), // the shared stream reconnected: fetch whatever was said meanwhile
    );
    void catchUp();
    return () => {
      stopped = true;
      unsubscribe();
    };
  }, [dealId]);

  return { events, status };
}

const ENDINGS = new Set(["negotiation_agreed", "negotiation_rejected", "negotiation_timeout", "negotiation_error"]);

/** What the room shows, derived only from the events received. */
export function summarize(events: NegotiationEvent[]) {
  const started = events.find((e) => e.event_type === "negotiation_started");
  const messages = events.filter((e) => e.event_type === "message_completed");
  const ending = [...events].reverse().find((e) => ENDINGS.has(e.event_type)) ?? null;
  const done = events.some((e) => e.event_type === "negotiation_completed");
  const last = events[events.length - 1];
  const thinking = !done && last?.event_type === "agent_thinking" ? last : null;
  const withOffer = messages.filter((m) => m.offer);
  const latest = (role: "advertiser" | "influencer") => [...withOffer].reverse().find((m) => m.agent_role === role)?.offer ?? null;
  const round = Math.max(0, ...events.map((e) => e.round ?? 0));
  const name = (role: "advertiser" | "influencer") => events.find((e) => e.agent_role === role)?.agent_name;
  return {
    started,
    messages,
    ending,
    done,
    thinking,
    round,
    maxRounds: started?.max_rounds ?? null,
    model: started?.compensation_model ?? "hybrid",
    current: withOffer[withOffer.length - 1]?.offer ?? null,
    advertiserOffer: latest("advertiser"),
    creatorAsk: latest("influencer"),
    advertiserName: name("advertiser"),
    creatorName: name("influencer"),
  };
}
