/* eslint-disable @typescript-eslint/require-await -- an in-memory adapter of an asynchronous port. */
import { LegacyPolicyUnreadable } from '../../../../src/modules/control-plane';
import type { PolicyLayer } from '../../../../src/modules/control-plane/domain/policy-alias';
import type { LegacyLayers, LegacyPolicySource } from '../../../../src/modules/control-plane/ports/policy-store';

/**
 * The legacy KV documents as policy layers, in memory (CPD-4's read-through
 * and migration cases). `failAfter` makes the store go down after that many
 * reads, as a KV outage part-way through a migration would.
 */
export class InMemoryLegacyPolicySource implements LegacyPolicySource {
  reads = 0;
  failAfter: number | null = null;
  private readonly orgsById = new Map<string, { layers: Map<string, PolicyLayer>; invalid: Map<string, string[]> }>();

  set(orgId: string, scope: string, layer: PolicyLayer | null): this {
    const org = this.org(orgId);
    if (layer === null) org.layers.delete(scope);
    else org.layers.set(scope, structuredClone(layer));
    return this;
  }

  refuse(orgId: string, scope: string, sections: string[]): this {
    this.org(orgId).invalid.set(scope, sections);
    return this;
  }

  async orgs(): Promise<string[]> {
    this.read();
    return [...this.orgsById.keys()];
  }

  async layersOf(orgId: string): Promise<LegacyLayers> {
    this.read();
    const org = this.orgsById.get(orgId);
    return {
      layers: new Map([...(org?.layers ?? new Map<string, PolicyLayer>())].map(([scope, layer]) => [scope, structuredClone(layer)])),
      invalid: new Map(org?.invalid ?? []),
    };
  }

  private read(): void {
    this.reads += 1;
    if (this.failAfter !== null && this.reads > this.failAfter) {
      throw new LegacyPolicyUnreadable(new Error('KV store unreachable'));
    }
  }

  private org(orgId: string): { layers: Map<string, PolicyLayer>; invalid: Map<string, string[]> } {
    let org = this.orgsById.get(orgId);
    if (org === undefined) {
      org = { layers: new Map(), invalid: new Map() };
      this.orgsById.set(orgId, org);
    }
    return org;
  }
}
