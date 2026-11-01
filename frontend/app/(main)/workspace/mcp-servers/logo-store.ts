'use client';

import { useEffect } from 'react';
import { create } from 'zustand';
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

export function ensureMcpLogosLoaded(): Promise<void> {
  if (loaded) return Promise.resolve();
  if (loading) return loading;
  if (Date.now() - failedAt < RETRY_AFTER_MS) return Promise.resolve();
  // Started inside the chain so a call that throws at once is a failed read too, not an error
  // thrown into the component that asked.
  loading = Promise.resolve()
    .then(() => McpServersApi.getLogos())
    .then((response) => {
      useMcpLogoStore.setState({ logos: { ...(response?.logos ?? {}) } });
      loaded = true;
    })
    .catch(() => {
      failedAt = Date.now();
    })
    .finally(() => {
      loading = null;
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
  useMcpLogoStore.setState((state) => {
    const logos = { ...state.logos };
    if (logo) logos[instanceId] = logo;
    else delete logos[instanceId];
    return { logos };
  });
}

/** Forget every logo, as if the page had just opened. */
export function resetMcpLogos(): void {
  loaded = false;
  loading = null;
  failedAt = Number.NEGATIVE_INFINITY;
  useMcpLogoStore.setState({ logos: {} });
}

/** The server's uploaded logo (a PNG data URL), or null. */
export function useMcpServerLogo(instanceId: string | null | undefined): string | null {
  const logo = useMcpLogoStore((state) => (instanceId ? state.logos[instanceId] ?? null : null));
  useEffect(() => {
    if (instanceId) void ensureMcpLogosLoaded();
  }, [instanceId]);
  return logo;
}
