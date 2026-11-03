import type { LastSyncSummary } from '../types';

/** A named set of sync custom field values; `id` is also its i18n key under `workspace.connectors.presets.<type>`. */
export interface ConnectorPreset {
  id: string;
  values: Record<string, unknown>;
}

export interface ConnectorUiExtras {
  /** Group whose section shows the preset chips. */
  presetGroup: string;
  presets: ConnectorPreset[];
  /** Suggests a preset id for a new connector from its field values. */
  suggestPreset?: (values: Record<string, unknown>) => string | null;
  /** NUMBER field that caps saved pages; the last sync's count is shown under it. */
  pageLimitField?: string;
}

const WEB_PRESETS: ConnectorPreset[] = [
  {
    id: 'docs',
    values: { type: 'recursive', discovery: 'sitemap_and_links', restrict_to_start_path: true, depth: 5, max_pages: 1000 },
  },
  {
    id: 'helpCenter',
    values: { type: 'recursive', discovery: 'sitemap_and_links', restrict_to_start_path: true, depth: 5, max_pages: 2000 },
  },
  {
    id: 'wholeSite',
    values: { type: 'recursive', discovery: 'sitemap_and_links', restrict_to_start_path: false, depth: 3, max_pages: 1000 },
  },
  { id: 'onePage', values: { type: 'single' } },
];

export function suggestWebPreset(values: Record<string, unknown>): string | null {
  let path: string;
  try {
    const raw = String(values.url ?? '').trim();
    if (!raw) return null;
    path = new URL(/^[a-z][a-z\d+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`).pathname.toLowerCase();
  } catch {
    return null;
  }
  if (/(^|\/)docs(\/|$)/.test(path)) return 'docs';
  if (/(^|\/)(hc|help|support)(\/|$)/.test(path)) return 'helpCenter';
  return null;
}

/** Keyed by {@link connectorTypeKey}. */
const CONNECTOR_UI_EXTRAS: Record<string, ConnectorUiExtras> = {
  web: { presetGroup: 'website', presets: WEB_PRESETS, suggestPreset: suggestWebPreset, pageLimitField: 'max_pages' },
};

export function getConnectorUiExtras(typeKey: string): ConnectorUiExtras | null {
  return CONNECTOR_UI_EXTRAS[typeKey] ?? null;
}

/** Form values hold "true"/"false" and "5" as often as true/false and 5, so compare as strings. */
const same = (a: unknown, b: unknown) => String(a) === String(b);

/** The preset the values match on every key it sets, or null ("Custom"). */
export function matchPreset(
  presets: ConnectorPreset[],
  values: Record<string, unknown>,
  defaults: Record<string, unknown> = {}
): string | null {
  const current = (key: string) => values[key] ?? defaults[key];
  return presets.find((p) => Object.entries(p.values).every(([k, v]) => same(v, current(k))))?.id ?? null;
}

/** Last sync counts for the page limit; null when no run has reported how many pages it keeps. */
export function pageLimitStatus(
  summary: LastSyncSummary | null | undefined,
  limit: unknown
): { saved: number; stoppedAtLimit: boolean; leftOut: number; nearLimit: boolean } | null {
  // pagesSaved counts this run's writes (near 0 on an unchanged re-sync); the limit caps pages kept.
  if (!summary || typeof summary.pagesStored !== 'number') return null;
  const stored = summary.pagesStored;
  const max = Number(limit);
  return {
    saved: stored,
    stoppedAtLimit: summary.stopReason === 'max_pages',
    leftOut: summary.skippedMaxPages ?? 0,
    nearLimit: Number.isFinite(max) && max > 0 && stored >= 0.9 * max,
  };
}
