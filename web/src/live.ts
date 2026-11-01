// Data hooks. Pages load from the REST API and reload when the server pushes a matching event
// over Server-Sent Events (/api/stream) — no polling, no fixtures.

import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveEvent } from "./types";

type Listener = (event: LiveEvent) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;
let connected = false;
const connectionListeners = new Set<(up: boolean) => void>();

function setConnected(up: boolean) {
  const reconnected = up && !connected;
  connected = up;
  connectionListeners.forEach((l) => l(up));
  if (reconnected) reconnectListeners.forEach((l) => l());
}

const reconnectListeners = new Set<() => void>();

/** Every live event, unfiltered and undebounced, plus a callback whenever the stream (re)connects so the caller can
 * fetch what it missed while it was down. Returns the unsubscribe function. */
export function subscribeLive(listener: Listener, onReconnect?: () => void): () => void {
  ensureStream();
  listeners.add(listener);
  if (onReconnect) reconnectListeners.add(onReconnect);
  return () => {
    listeners.delete(listener);
    if (onReconnect) reconnectListeners.delete(onReconnect);
  };
}

function ensureStream() {
  if (source) return;
  source = new EventSource("/api/stream", { withCredentials: true });
  source.onopen = () => setConnected(true);
  source.onerror = () => setConnected(false); // EventSource reconnects on its own (retry: 3000)
  source.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data) as LiveEvent;
      listeners.forEach((l) => l(event));
    } catch {
      /* keep-alive or malformed */
    }
  };
}

export function closeStream() {
  source?.close();
  source = null;
  setConnected(false);
}

/** Calls `handler` for live events that pass `filter`, at most once per `delayMs`: a burst triggers a reload shortly
 * after its first event and again after the next, never waits for the burst to end (agents can talk for minutes). */
export function useLiveEvents(filter: (e: LiveEvent) => boolean, handler: () => void, delayMs = 250) {
  const filterRef = useRef(filter);
  const handlerRef = useRef(handler);
  filterRef.current = filter;
  handlerRef.current = handler;
  useEffect(() => {
    ensureStream();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const listener: Listener = (e) => {
      if (!filterRef.current(e) || timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        handlerRef.current();
      }, delayMs);
    };
    listeners.add(listener);
    return () => {
      clearTimeout(timer);
      listeners.delete(listener);
    };
  }, [delayMs]);
}

export function useStreamConnected(): boolean {
  const [up, setUp] = useState(connected);
  useEffect(() => {
    ensureStream();
    connectionListeners.add(setUp);
    return () => {
      connectionListeners.delete(setUp);
    };
  }, []);
  return up;
}

export interface Resource<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Fetches once per `key` and exposes `reload` for live updates. Keeps the last good data while reloading.
 * Reloads never cancel each other: one request runs at a time, and reloads asked for meanwhile become one more request
 * after it. (Dropping every response but the newest starved the page while agents talked: events kept arriving faster
 * than the server answered, so nothing was ever shown until the conversation ended.) */
export function useResource<T>(load: () => Promise<T>, key: string): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;
  const generation = useRef(0); // bumped when `key` changes: a response for the old key is never shown
  const inflight = useRef<Promise<void> | null>(null);
  const again = useRef(false);

  const reload = useCallback((): Promise<void> => {
    if (inflight.current) {
      again.current = true;
      return inflight.current;
    }
    const run = async () => {
      do {
        again.current = false;
        const gen = generation.current;
        try {
          const value = await loadRef.current();
          if (gen === generation.current) {
            setData(value);
            setError(null);
          }
        } catch (e) {
          if (gen === generation.current) setError(e instanceof Error ? e.message : String(e));
        } finally {
          if (gen === generation.current) setLoading(false);
        }
      } while (again.current);
      inflight.current = null;
    };
    inflight.current = run();
    return inflight.current;
  }, []);

  useEffect(() => {
    generation.current += 1;
    setLoading(true);
    setData(undefined);
    void reload();
  }, [key, reload]);

  return { data, error, loading, reload };
}
