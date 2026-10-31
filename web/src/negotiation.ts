// Live negotiation transcript for one deal. The server replays stored events, then pushes new ones the moment an
// agent produces them; each frame has an `id`, so EventSource resumes with Last-Event-ID after a drop and we
// ignore anything at or below the last event_id we kept. Reading the stream never starts or re-runs a negotiation.

import { useEffect, useState } from "react";
import { api } from "./api";
import type { NegotiationEvent } from "./types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "done" | "legacy" | "error";

export function useNegotiationStream(dealId: string): {
  events: NegotiationEvent[];
  status: StreamStatus;
} {
  const [events, setEvents] = useState<NegotiationEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  useEffect(() => {
    setEvents([]);
    setStatus("connecting");
    let last = 0;
    let closed = false;
    const source = new EventSource(api.negotiationStreamUrl(dealId), {
      withCredentials: true,
    });
    const close = (s: StreamStatus) => {
      closed = true;
      source.close();
      setStatus(s);
    };
    source.onopen = () => !closed && setStatus("live");
    source.onerror = () => {
      if (closed) return;
      // CLOSED means the server refused (signed out, not your deal): EventSource will not retry on its own
      setStatus(source.readyState === EventSource.CLOSED ? "error" : "reconnecting");
    };
    source.onmessage = (msg) => {
      let e: NegotiationEvent;
      try {
        e = JSON.parse(msg.data) as NegotiationEvent;
      } catch {
        return;
      }
      if (e.event_type === "negotiation_legacy") return close("legacy");
      if (e.event_id <= last) return; // already shown before the reconnect
      last = e.event_id;
      setEvents((prev) => [...prev, e]);
      if (e.event_type === "negotiation_completed") close("done");
    };
    return () => {
      closed = true;
      source.close();
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
