'use client';

import { useCallback, useRef, useState } from 'react';
import { AIModelsApi } from '../api';
import type { DiscoveredModel } from '../types';

export function canFetchModels(
  requiredFields: string[],
  values: Record<string, unknown>,
  savedFields: Set<string>,
): boolean {
  return requiredFields.every((name) => {
    if (savedFields.has(name)) return true;
    return String(values[name] ?? '').trim() !== '';
  });
}

export function useModelDiscovery() {
  const abortRef = useRef<AbortController | null>(null);
  const [models, setModels] = useState<DiscoveredModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [fetched, setFetched] = useState(false);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setModels([]);
    setError(null);
    setWarnings([]);
    setLoading(false);
    setFetched(false);
  }, []);

  const fetchModels = useCallback(async (body: {
    provider: string;
    capability?: string;
    configuration?: Record<string, unknown>;
    modelKey?: string;
    query?: string;
  }) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await AIModelsApi.discoverModels(body, controller.signal);
      if (controller.signal.aborted) return;
      setModels(result.models ?? []);
      setWarnings(result.warnings ?? []);
      setFetched(true);
      if (result.errorCode || result.success === false) {
        setError(result.message || result.errorCode || 'discovery_failed');
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : 'discovery_failed';
      setError(message);
      setModels([]);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, []);

  return { models, loading, error, warnings, fetched, fetchModels, reset };
}
