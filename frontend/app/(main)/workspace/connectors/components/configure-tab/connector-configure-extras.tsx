'use client';

/** Extras at the bottom of a connector's configure tab. Editions plug in via config.ts. */
export function ConnectorConfigureExtras(_props: {
  connectorId: string | null;
  connectorType: string;
  connectorName: string;
  scope: string;
}) {
  return null;
}
