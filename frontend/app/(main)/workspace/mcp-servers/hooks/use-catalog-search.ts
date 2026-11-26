'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { McpServersApi } from '../api';
import type { McpServerTemplate } from '../types';

// Long enough that typing a phrase asks the server once, short enough to feel live.
export const DISCOVER_SEARCH_DELAY_MS = 250;

/**
 * The catalog entries in `offered` that match `query`, in the backend's ranking (names, then what
 * servers do), each with the capability it matched. `null` while there is no query; empty until the query's answer arrives.
 */
export function useCatalogSearch(query: string, offered: McpServerTemplate[]) {
  const q = query.trim();
  const [found, setFound] = useState<{ query: string; templates: McpServerTemplate[] } | null>(null);
  const request = useRef(0);

  useEffect(() => {
    const id = ++request.current;
    if (!q) {
      setFound(null);
      return;
    }
    const timer = setTimeout(() => {
      McpServersApi.getCatalog({ search: q, limit: 200 })
        .then((res) => {
          if (id === request.current) setFound({ query: q, templates: res.templates });
        })
        .catch(() => {
          // A failed search falls back to names and descriptions here rather than showing nothing.
          if (id !== request.current) return;
          const needle = q.toLowerCase();
          setFound({
            query: q,
            templates: offered.filter(
              (tpl) => tpl.displayName.toLowerCase().includes(needle) || tpl.description.toLowerCase().includes(needle)
            ),
          });
        });
    }, DISCOVER_SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [q, offered]);

  const results = useMemo(() => {
    if (!q) return null;
    // Until this query's answer arrives, show nothing rather than another query's servers.
    if (found?.query !== q) return [];
    // Only entries this person may add, keeping the server's order and the matched capability.
    const byType = new Map(offered.map((tpl) => [tpl.typeId, tpl]));
    return found.templates
      .filter((tpl) => byType.has(tpl.typeId))
      .map((tpl) => ({ ...byType.get(tpl.typeId)!, matchedCapability: tpl.matchedCapability }));
  }, [q, found, offered]);

  return { results, searching: Boolean(q) && found?.query !== q };
}
