import { configPaths } from '../../../configuration_manager/paths/paths';
import { SANDBOX_PLANE_ENDPOINTS } from '../../../tokens_manager/services/cm.service';
import { normalizeUrl } from '../../../tokens_manager/utils/utils';
import type { SealedEndpoints } from '../../domain/component-keys';
import type { EndpointsSource } from './sandbox-broker-endpoint';

/**
 * The sandbox-plane addresses Node seals into each component's document (R38IDENT-FU-10, SEC-D-03 fix 3), in the
 * shape of `/services/endpoints`: the broker, the proxy's control plane and the two gateways, and nothing else a
 * component reads. Node's own environment wins, as it does when Node writes the plain document
 * (`ConfigService.getSandboxPlaneEndpoints`); a store that cannot be read gives the environment's alone.
 */
export async function sandboxPlaneEndpoints(
  store: EndpointsSource,
  env: Readonly<Record<string, string | undefined>> = process.env,
): Promise<SealedEndpoints> {
  let stored: Record<string, Record<string, unknown> | undefined> = {};
  try {
    const raw = await store.get<string>(configPaths.endpoint);
    const parsed: unknown = raw === null ? null : JSON.parse(raw);
    if (parsed !== null && typeof parsed === 'object')
      stored = parsed as typeof stored;
  } catch {
    stored = {};
  }
  const sealed: SealedEndpoints = {};
  for (const [service, key, envName] of SANDBOX_PLANE_ENDPOINTS) {
    const kept = stored[service]?.[key];
    const url =
      normalizeUrl(env[envName] ?? '') ||
      (typeof kept === 'string' ? normalizeUrl(kept) : '');
    if (url) sealed[service] = { ...sealed[service], [key]: url };
  }
  return sealed;
}
