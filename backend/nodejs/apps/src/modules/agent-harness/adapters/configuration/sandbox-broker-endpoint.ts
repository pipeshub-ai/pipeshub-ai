import { configPaths } from '../../../configuration_manager/paths/paths';

/** What reads the deployment's endpoints document (the key-value store). */
export interface EndpointsSource {
  get<T>(key: string): Promise<T | null>;
}

/**
 * Where the sandbox broker answers: `/services/endpoints` `sandboxBroker.endpoint`
 * (what every Python process reads), else `SANDBOX_BROKER_BACKEND`; null when
 * neither names one. Read on every call, so a moved broker needs no restart.
 */
export async function sandboxBrokerEndpoint(
  store: EndpointsSource,
  env: NodeJS.ProcessEnv = process.env,
): Promise<string | null> {
  let configured: unknown;
  try {
    const raw = await store.get<string>(configPaths.endpoint);
    configured =
      raw === null
        ? null
        : (JSON.parse(raw) as Record<string, unknown>)['sandboxBroker'];
  } catch {
    configured = null;
  }
  const endpoint =
    configured !== null && typeof configured === 'object'
      ? (configured as Record<string, unknown>)['endpoint']
      : undefined;
  if (typeof endpoint === 'string' && endpoint.length > 0) return endpoint;
  const fromEnv = env['SANDBOX_BROKER_BACKEND'];
  return fromEnv !== undefined && fromEnv.length > 0 ? fromEnv : null;
}
