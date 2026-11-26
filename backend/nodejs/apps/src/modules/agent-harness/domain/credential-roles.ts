/**
 * SEC-C-10 (ADR-CRED-01): the deployment's machine credentials, one per role. Each is a bearer secret Node
 * generates and rotates like the worker credential (a current one and the previous one for a grace window), and each
 * is accepted only on the routes its role calls, so a foothold in the scraper, the egress proxy or the broker no
 * longer claims any org's run.
 */
export const HARNESS_CREDENTIAL_ROLES = [
  'worker',
  'scrape',
  'egress-proxy',
  'sandbox-broker',
] as const;

export type HarnessCredentialRole = (typeof HARNESS_CREDENTIAL_ROLES)[number];

/** What each role is, for the ADR's table and the refusal log; never a credential. */
export const ROLE_PURPOSE: Readonly<Record<HarnessCredentialRole, string>> = {
  worker:
    'the harness worker, the inline claimer and Node itself: claims runs, reads the live layer and audit evidence, calls the broker and the egress control plane',
  scrape: "the deployment's Prometheus: reads /metrics only",
  'egress-proxy':
    "the egress proxy: mints a run's gateway token and reads the operator egress stops",
  'sandbox-broker':
    "the sandbox broker: registers a sandbox's bindings with the egress proxy",
};

/** The services that hold no SECRET_KEY (ADR-KEY-02): each gets only its own credential, sealed for it. */
export const SANDBOX_PLANE_COMPONENTS = {
  egressProxy: 'egress-proxy',
  sandboxBroker: 'sandbox-broker',
} as const satisfies Record<string, HarnessCredentialRole>;

export type SandboxPlaneComponent = keyof typeof SANDBOX_PLANE_COMPONENTS;

/**
 * Which roles a component's own listeners admit, by route family: the component verifies a digest of these roles'
 * accepted credentials, never the credentials themselves (so the proxy cannot present the broker's).
 */
export const COMPONENT_ACCEPTS: Readonly<
  Record<
    SandboxPlaneComponent,
    Readonly<Record<'control' | 'metrics', readonly HarnessCredentialRole[]>>
  >
> = {
  egressProxy: { control: ['worker', 'sandbox-broker'], metrics: ['scrape'] },
  sandboxBroker: { control: ['worker'], metrics: ['scrape'] },
};
