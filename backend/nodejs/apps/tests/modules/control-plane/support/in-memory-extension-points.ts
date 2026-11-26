/* eslint-disable @typescript-eslint/require-await -- in-memory adapters implement asynchronous ports without I/O. */
/**
 * A domain module made of in-memory extension points (03-interfaces §3.1), the
 * fixture the module conformance contract runs against before any production
 * module brings these points (CPD-7, CPD-8, CPD-9, CPD-12).
 */
import type { Problem } from '../../../../src/modules/control-plane/contracts/generated';
import type { CatalogItem, Descriptor } from '../../../../src/modules/control-plane/domain/catalog-item';
import type { Binding, ChangeKind, Installation } from '../../../../src/modules/control-plane/domain/objects';
import type { DimensionRow } from '../../../../src/modules/control-plane/domain/policy-alias';
import { problem } from '../../../../src/modules/control-plane/domain/problem';
import type { PinSubject } from '../../../../src/modules/control-plane/domain/resolution';
import type {
  ApprovalTriggers,
  BindingChoice,
  BindingIndex,
  BindingSelector,
  CatalogSource,
  ConfigValidator,
  DescriptorProvider,
  DescriptorSet,
  DimensionContributor,
  DomainModule,
  InstallExpander,
} from '../../../../src/modules/control-plane/ports/extension-points';
import { descriptorSetDigest, toolDescriptorDigest } from './digests';

/** Requires a string `url` starting with `https://`. */
export class InMemoryConfigValidator implements ConfigValidator {
  constructor(readonly kind: string) {}

  async validate(_item: CatalogItem, config: unknown): Promise<Problem[]> {
    const url = (config as { url?: unknown } | null)?.url;
    if (typeof url === 'string' && url.startsWith('https://')) return [];
    return [problem('CP_INVALID_CONFIG', 'The server address must be an https:// URL (field url).', { action: 'fix_config' })];
  }
}

/** Describes an installation from a fixed tool table, the way a driver lists a server's tools. */
export class InMemoryDescriptorProvider implements DescriptorProvider {
  constructor(
    readonly kind: string,
    private readonly tools: Readonly<Record<string, readonly string[]>>,
  ) {}

  async describe(inst: Installation): Promise<DescriptorSet> {
    const descriptors = [...(this.tools[inst.id] ?? [])].sort().map((name) => {
      const tool = { name, description: `${name} of ${inst.id}`, inputSchema: { type: 'object' }, effect: 'read' as const, sink: null };
      return { ...tool, digest: toolDescriptorDigest(tool) } as Descriptor;
    });
    return { descriptors, digest: descriptorSetDigest(descriptors.map((d) => [d.name, d.digest] as const)) };
  }
}

export class InMemoryApprovalTriggers implements ApprovalTriggers {
  constructor(readonly kind: string) {}

  changesOf(before: Installation | null, after: Installation): ChangeKind[] {
    if (before === null) return ['install'];
    const changes: ChangeKind[] = [];
    if (before.item !== after.item) changes.push('upgrade');
    if ((before.config as { url?: unknown }).url !== (after.config as { url?: unknown }).url) changes.push('url_change');
    return changes;
  }
}

const OWNER_OF: Record<Installation['credentialMode'], (subject: PinSubject) => string | null> = {
  per_user: (subject) => `user:${subject.actorId}`,
  agent: (subject) => (subject.agentKey === null ? null : `agent:${subject.agentKey}`),
  org: () => 'org',
  none: () => null,
};

/** The binding the installation's credential mode names, or why there is none. */
export class InMemoryBindingSelector implements BindingSelector {
  constructor(readonly kind: string) {}

  select(inst: Installation, subject: PinSubject, bindings: BindingIndex): BindingChoice {
    const owner = OWNER_OF[inst.credentialMode](subject);
    const binding: Binding | undefined = bindings.find((b) => b.installation === inst.id && b.owner === owner);
    if (binding === undefined) return { unavailable: 'credential_missing' };
    if (binding.status === 'revoked') return { unavailable: 'credential_revoked' };
    if (binding.status === 'expired') return { unavailable: 'credential_expired' };
    return { binding };
  }
}

export class InMemoryDimensionContributor implements DimensionContributor {
  constructor(readonly kind: string) {}

  dimensions(): readonly DimensionRow[] {
    return [
      { path: `catalog.kinds.${this.kind}.allowed`, kind: 'ceiling', order: 'bool-and', exposure: ['deployment', 'org', 'team'] },
      { path: `catalog.kinds.${this.kind}.trustRoots`, kind: 'operator', exposure: ['release', 'deployment'] },
    ];
  }
}

/** Expands an installation into one child per contributed item, as a plugin does. */
export class InMemoryInstallExpander implements InstallExpander {
  constructor(
    readonly kind: string,
    private readonly contributes: readonly string[],
  ) {}

  async expand(inst: Installation): Promise<Installation[]> {
    return this.contributes.map((item, n) => ({
      ...inst,
      id: `${inst.id}-c${n + 1}`,
      item,
      kind: item.slice(0, item.indexOf(':')),
      installedVia: inst.id,
      approved: null,
      status: 'pending_approval',
      revision: 1,
    }));
  }
}

const catalogOf = (kind: string): CatalogSource => ({
  kind,
  releaseItems: () => [],
  descriptorDigest: (descriptor) => toolDescriptorDigest(descriptor as unknown as Record<string, unknown>),
});

/** A module with every extension point, all in memory. */
export function inMemoryDomainModule(kind = 'demo'): DomainModule {
  return {
    kind,
    catalog: catalogOf(kind),
    validator: new InMemoryConfigValidator(kind),
    descriptors: new InMemoryDescriptorProvider(kind, { inst_01J9ZQ5Y8K3M4N5P6Q7R8S0001: ['list_issues', 'create_issue'] }),
    approvals: new InMemoryApprovalTriggers(kind),
    bindings: new InMemoryBindingSelector(kind),
    dimensions: new InMemoryDimensionContributor(kind),
    expander: new InMemoryInstallExpander(kind, ['skill:org/report@1', 'mcp:pipeshub/linear@3']),
  };
}
