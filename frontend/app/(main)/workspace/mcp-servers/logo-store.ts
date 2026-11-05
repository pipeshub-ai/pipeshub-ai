'use client';

import { useEffect } from 'react';
import { create } from 'zustand';
import { captureSession, onSessionEnd } from '@/lib/api/session-cache';
import { McpServersApi } from './api';

/**
 * Logos uploaded for custom MCP servers, by instance id. Read once per page session with one
 * request, the first time an icon asks, so the pages that show servers need no extra field.
 */
interface McpLogoState {
  logos: Record<string, string>;
}

const useMcpLogoStore = create<McpLogoState>(() => ({ logos: {} }));

/** A failed read is tried again no sooner than this, however many icons ask. */
const RETRY_AFTER_MS = 60_000;

let loaded = false;
let loading: Promise<void> | null = null;
let failedAt = Number.NEGATIVE_INFINITY;
// Logos saved or removed on this page while a read is on its way: the read's answer is older.
let changedDuringRead: Map<string, string | null> | null = null;

function withLogo(logos: Record<string, string>, instanceId: string, logo: string | null): Record<string, string> {
  const next = { ...logos };
  if (logo) next[instanceId] = logo;
  else delete next[instanceId];
  return next;
}

export function ensureMcpLogosLoaded(): Promise<void> {
  if (loaded) return Promise.resolve();
  if (loading) return loading;
  if (Date.now() - failedAt < RETRY_AFTER_MS) return Promise.resolve();
  const changed = new Map<string, string | null>();
  changedDuringRead = changed;
  // A read still on its way when the session ends must not touch the next session's state.
  const isCurrent = captureSession();
  // Started inside the chain so a call that throws at once is a failed read too, not an error
  // thrown into the component that asked.
  loading = Promise.resolve()
    .then(() => McpServersApi.getLogos())
    .then((response) => {
      if (!isCurrent()) return;
      let logos = { ...(response?.logos ?? {}) };
      for (const [instanceId, logo] of changed) logos = withLogo(logos, instanceId, logo);
      useMcpLogoStore.setState({ logos });
      loaded = true;
    })
    .catch(() => {
      if (isCurrent()) failedAt = Date.now();
    })
    .finally(() => {
      if (!isCurrent()) return;
      loading = null;
      if (changedDuringRead === changed) changedDuringRead = null;
    });
  return loading;
}

/** Read the logos again, e.g. when a servers page is refreshed. */
export function reloadMcpLogos(): Promise<void> {
  loaded = false;
  failedAt = Number.NEGATIVE_INFINITY;
  return ensureMcpLogosLoaded();
}

/** After a logo was saved or removed here, so every icon on the page shows it at once. */
export function setMcpServerLogo(instanceId: string, logo: string | null): void {
  changedDuringRead?.set(instanceId, logo);
  useMcpLogoStore.setState((state) => ({ logos: withLogo(state.logos, instanceId, logo) }));
}

/** Forget every logo, as if the page had just opened. */
export function resetMcpLogos(): void {
  loaded = false;
  loading = null;
  changedDuringRead = null;
  failedAt = Number.NEGATIVE_INFINITY;
  useMcpLogoStore.setState({ logos: {} });
}

onSessionEnd(resetMcpLogos);

/** The server's uploaded logo (a PNG data URL), or null. */
export function useMcpServerLogo(instanceId: string | null | undefined): string | null {
  const logo = useMcpLogoStore((state) => (instanceId ? state.logos[instanceId] ?? null : null));
  useEffect(() => {
    if (instanceId) void ensureMcpLogosLoaded();
  }, [instanceId]);
  return logo;
}
