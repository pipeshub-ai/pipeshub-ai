'use client';

import { useCallback, useEffect, useState } from 'react';
import { WebSearchApi } from '../../../workspace/web-search/api';
import type { ConfiguredWebSearchProvider } from '../../../workspace/web-search/types';

interface UseWebSearchConfigResult {
  configuredProviders: ConfiguredWebSearchProvider[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

/** Wraps WebSearchApi.getConfig with a tiny loading/error state for builder-side UIs. */
export function useWebSearchConfig(enabled: boolean = true): UseWebSearchConfigResult {
  const [configuredProviders, setConfiguredProviders] = useState<ConfiguredWebSearchProvider[]>([]);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const config = await WebSearchApi.getConfig();
      setConfiguredProviders(config.providers);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load web search providers');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void reload();
  }, [enabled, reload]);

  return { configuredProviders, loading, error, reload };
}
