import { useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import type { ReadonlyURLSearchParams } from 'next/navigation';

/** Case-insensitive connector type key (strip spaces, hyphens, underscores). */
export function normalizeConnectorTypeKey(value: string): string {
  return value.trim().toLowerCase();
}

/**
 * Map a URL `connectorType` query value to the canonical registry `type`
 * (e.g. ONEDRIVE → OneDrive). Returns null when param is empty or unknown.
 */
export function resolveConnectorTypeParam(
  param: string | null | undefined,
  registry: ReadonlyArray<{ type: string }>,
  active: ReadonlyArray<{ type: string }>,
): string | null {
  if (!param?.trim()) return null;

  const key = normalizeConnectorTypeKey(param);
  const match =
    registry.find((c) => normalizeConnectorTypeKey(c.type) === key) ??
    active.find((c) => normalizeConnectorTypeKey(c.type) === key);

  return match?.type ?? null;
}

type ConnectorTypeScopePath =
  | '/workspace/connectors/team/'
  | '/workspace/connectors/personal/';

/**
 * Resolve `?connectorType=` from the URL to the canonical registry type and
 * replace the address bar when the raw param differs (e.g. ONEDRIVE → OneDrive).
 */
export function useResolvedConnectorTypeParam(
  searchParams: ReadonlyURLSearchParams,
  registryConnectors: ReadonlyArray<{ type: string }>,
  activeConnectors: ReadonlyArray<{ type: string }>,
  basePath: ConnectorTypeScopePath,
): {
  /** Canonical registry type once lists are loaded; null while unresolved. */
  connectorType: string | null;
  /** True when any non-empty connectorType query param is present. */
  showConnectorTypePage: boolean;
} {
  const router = useRouter();
  const connectorTypeParam = searchParams.get('connectorType');

  const connectorType = useMemo(
    () =>
      resolveConnectorTypeParam(
        connectorTypeParam,
        registryConnectors,
        activeConnectors,
      ),
    [connectorTypeParam, registryConnectors, activeConnectors],
  );

  useEffect(() => {
    if (!connectorTypeParam || !connectorType || connectorTypeParam === connectorType) {
      return;
    }

    const params = new URLSearchParams(searchParams.toString());
    params.set('connectorType', connectorType);
    const query = params.toString();
    router.replace(query ? `${basePath}?${query}` : basePath);
  }, [connectorTypeParam, connectorType, searchParams, router, basePath]);

  return {
    connectorType,
    showConnectorTypePage: Boolean(connectorTypeParam?.trim()),
  };
}
