import { metricsBackend } from '../metrics-backend';

const installInfo = metricsBackend.createGauge({
  name: 'pipeshub_install_info',
  help: 'Infrastructure backends used by this install (value is always 1)',
  labelNames: [
    'graph_db',
    'vector_db',
    'message_broker',
    'kv_store',
    'edition',
  ],
});

// Set by the build, not by an operator: the EE image sets
// PIPESHUB_EDITION=enterprise. EE ships with telemetry off, and only
// PipesHub's own SaaS turns it on, so enterprise data at the collector is
// SaaS data. Unknown values collapse so the label stays bounded.
const EDITIONS: ReadonlySet<string> = new Set(['community', 'enterprise']);

export function editionLabel(env: NodeJS.ProcessEnv = process.env): string {
  const value = (env.PIPESHUB_EDITION || 'community').trim().toLowerCase();
  return EDITIONS.has(value) ? value : 'other';
}

export function setInstallInfo(labels: {
  graph_db: string;
  vector_db: string;
  message_broker: string;
  kv_store: string;
}): void {
  installInfo.reset();
  installInfo.set({ ...labels, edition: editionLabel() }, 1);
}
