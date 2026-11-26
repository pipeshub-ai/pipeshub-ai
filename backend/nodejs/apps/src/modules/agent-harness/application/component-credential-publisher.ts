import { isDeepStrictEqual } from 'node:util';
import type { SecretKeyRing } from '../../../libs/encryptor/secret-key-ring';
import {
  acceptedDigests,
  ComponentDocument,
  egressSealingKeys,
  SealedEndpoints,
} from '../domain/component-keys';
import {
  COMPONENT_ACCEPTS,
  HarnessCredentialRole,
  SANDBOX_PLANE_COMPONENTS,
  SandboxPlaneComponent,
} from '../domain/credential-roles';
import { ConfigDocumentStore } from '../ports/config-document-store';
import { updateConfigDocument } from './config-documents';
import { HarnessAuthority } from './harness-authority';
import { HarnessLogger, silentHarnessLogger } from './harness-log';

/** Gives a component its own store login (a Redis ACL user), scoped to the keys it may touch (ADR-KEY-02 D3). */
export interface ComponentStoreLogins {
  apply(component: SandboxPlaneComponent): Promise<void>;
}

/** A component's document store, sealed under that component's own key; absent when the deployment runs none. */
export type ComponentDocumentStores = Readonly<
  Partial<Record<SandboxPlaneComponent, ConfigDocumentStore<ComponentDocument>>>
>;

export interface ComponentCredentialPublisherOptions {
  authority: HarnessAuthority;
  documents: ComponentDocumentStores;
  /** The deployment's ring; null where this process holds none (then the proxy gets no sealing keys). */
  ring: () => SecretKeyRing | null;
  logins?: ComponentStoreLogins | null;
  /** R38IDENT-FU-10: the sandbox-plane addresses each document carries; absent (a test's world), none. */
  endpoints?: () => Promise<SealedEndpoints>;
  logger?: HarnessLogger;
}

/**
 * ADR-KEY-02: Node, which holds `SECRET_KEY`, seals for each sandbox-plane component only what that component needs:
 * its own role credential, digests of the credentials it admits (SEC-C-10), and, for the egress proxy, the egress
 * sealing keys derived from the ring, and the sandbox-plane addresses it calls (R38IDENT-FU-10), so a writer of the
 * plain endpoints document cannot redirect it. Run after every provisioning and rotation, and on a timer so a store restart
 * (a Redis that lost its ACL users) heals on its own. Idempotent: an unchanged document is not rewritten.
 */
export class ComponentCredentialPublisher {
  private readonly logger: HarnessLogger;

  constructor(private readonly options: ComponentCredentialPublisherOptions) {
    this.logger = options.logger ?? silentHarnessLogger;
  }

  /** The components published; one that cannot be is logged and skipped, never the others. */
  async publish(): Promise<SandboxPlaneComponent[]> {
    const published: SandboxPlaneComponent[] = [];
    for (const component of Object.keys(
      SANDBOX_PLANE_COMPONENTS,
    ) as SandboxPlaneComponent[]) {
      const store = this.options.documents[component];
      if (store === undefined) continue;
      try {
        const document = await this.document(component);
        if (document === null) continue;
        await updateConfigDocument(store, (current) =>
          current !== null && isDeepStrictEqual(current, document)
            ? null
            : document,
        );
        await this.options.logins?.apply(component);
        published.push(component);
      } catch (error) {
        this.logger.warn('agent_harness.component_publish_failed', {
          component,
          errorType: error instanceof Error ? error.name : typeof error,
          // R38IDENT-FU-12: a store that refuses ACL SETUSER says what its operator must do.
          ...(error instanceof Error && error.name === 'ComponentLoginMissing'
            ? { action: error.message }
            : {}),
        });
      }
    }
    return published;
  }

  private async document(
    component: SandboxPlaneComponent,
  ): Promise<ComponentDocument | null> {
    const role = SANDBOX_PLANE_COMPONENTS[component];
    const own = await this.options.authority.credentialSetFor(role);
    if (own === null) return null;
    const accepts: ComponentDocument['accepts'] = {};
    const families = COMPONENT_ACCEPTS[component];
    const roles = new Set<HarnessCredentialRole>([
      ...families.control,
      ...families.metrics,
    ]);
    for (const admitted of roles) {
      const set = await this.options.authority.credentialSetFor(admitted);
      if (set !== null) accepts[admitted] = acceptedDigests(set);
    }
    const ring = component === 'egressProxy' ? this.options.ring() : null;
    const endpoints = await this.options.endpoints?.();
    return {
      role,
      credential: own,
      accepts,
      ...(ring !== null ? { egressSealing: egressSealingKeys(ring) } : {}),
      ...(endpoints !== undefined ? { endpoints } : {}),
    };
  }
}
