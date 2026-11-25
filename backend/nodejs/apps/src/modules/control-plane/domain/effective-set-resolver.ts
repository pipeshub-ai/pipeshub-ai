import type { Problem } from '../contracts/generated';
import { DEFAULT_EFFECTIVE_SET_CAP, REASON_CODES } from './effective-set';
import type {
  Binding,
  EffectiveItem,
  EffectiveSet,
  GrantEntry,
  Installation,
  SubjectRef,
  UnavailableReason,
} from './objects';
import {
  kindAllowed,
  TOOLS_EFFECTS,
  TOOLS_GRANT,
  type EffectiveRunConfig,
} from './policy-alias';
import { problem } from './problem';
import type { PinSubject, Snapshot, ToolEffect } from './resolution';

type ToolGrant = Readonly<Record<string, readonly string[] | null>>;
type VisibleVia = EffectiveItem['provenance']['visibleVia'];

interface Granted {
  readonly entry: GrantEntry;
  readonly grantedBy: SubjectRef;
}

export interface EffectiveSetOptions {
  /** The canonical digest (libs `canonicalDigest`); the domain does not hash. */
  readonly digest: (value: unknown) => string;
  readonly cap?: number;
}

const byKey = <T>(items: readonly T[], key: (item: T) => string): T[] =>
  [...items].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));

/** The grants a run uses (02-lld §5.2): the agent's, or the assistant's org, user and team grants merged. */
function grantedEntries(subject: PinSubject, snapshot: Snapshot): Granted[] {
  const subjects: SubjectRef[] =
    subject.agentKey !== null
      ? [`agent:${subject.agentKey}`]
      : [
          'org',
          `user:${subject.actorId}`,
          ...snapshot.actorTeams.map((team): SubjectRef => `team:${team}`),
        ];
  const merged = new Map<string, Granted>();
  for (const grantedBy of subjects) {
    for (const entry of snapshot.grants.get(grantedBy)?.entries ?? []) {
      const seen = merged.get(entry.installation);
      if (seen === undefined) {
        merged.set(entry.installation, { entry, grantedBy });
        continue;
      }
      const tools =
        seen.entry.tools === null || entry.tools === null
          ? null
          : [...new Set([...seen.entry.tools, ...entry.tools])];
      merged.set(entry.installation, {
        entry: {
          installation: entry.installation,
          tools,
          required: seen.entry.required || entry.required,
        },
        grantedBy: seen.grantedBy,
      });
    }
  }
  return byKey([...merged.values()], (granted) => granted.entry.installation);
}

/**
 * Who may see an installation in this run: org-wide, the actor's own, a team
 * the actor is in, or (in an agent's run) what the agent's owner put on it,
 * which CPS's skills rule treats as shared with whoever runs the agent.
 */
function visibleVia(
  inst: Installation,
  subject: PinSubject,
  snapshot: Snapshot,
  grantedBy: SubjectRef,
): VisibleVia {
  if (inst.visibility === 'org') return 'org';
  if (inst.owner === `user:${subject.actorId}`) return 'owner';
  if (
    inst.visibility === 'team' &&
    snapshot.actorTeams.some((team) => inst.owner === `team:${team}`)
  ) {
    return 'team';
  }
  const sharedByOwner =
    subject.agentKey !== null &&
    grantedBy === `agent:${subject.agentKey}` &&
    typeof subject.agentOwner === 'string' &&
    inst.owner === `user:${subject.agentOwner}`;
  return sharedByOwner ? 'owner' : null;
}

const CREDENTIAL_OWNER: Record<
  Exclude<Installation['credentialMode'], 'none'>,
  (subject: PinSubject) => string | null
> = {
  per_user: (subject) => `user:${subject.actorId}`,
  agent: (subject) =>
    subject.agentKey === null ? null : `agent:${subject.agentKey}`,
  org: () => 'org',
};

function bindingFor(
  inst: Installation,
  subject: PinSubject,
  bindings: readonly Binding[],
): { binding: Binding | null } | { unavailable: UnavailableReason } {
  if (inst.credentialMode === 'none') return { binding: null };
  const owner = CREDENTIAL_OWNER[inst.credentialMode](subject);
  const binding = bindings.find(
    (b) => b.installation === inst.id && b.owner === owner,
  );
  if (
    binding === undefined ||
    binding.secret === null ||
    binding.status === 'pending' ||
    binding.status === 'error'
  ) {
    return { unavailable: 'credential_missing' };
  }
  if (binding.status === 'revoked')
    return { unavailable: 'credential_revoked' };
  if (binding.status === 'expired')
    return { unavailable: 'credential_expired' };
  return { binding };
}

/** `mcp@<installation>/<tool>`, or `<toolset name>@<installation>/<tool>` (today's pinned tool ids). */
const toolIdOf = (inst: Installation, tool: string): string => {
  const kind =
    inst.kind === 'mcp'
      ? 'mcp'
      : inst.item.slice(inst.item.indexOf('/') + 1, inst.item.lastIndexOf('@'));
  return `${kind}@${inst.id}/${tool}`;
};

/**
 * 02-lld §5.2 with `tools.effects`: grant ∩ visible ∩ enabled ∩ approved ∩
 * `tools.grant` ∩ effects. Every granted item is listed, ready or with the
 * first failing reason in the documented order. Pure: reads the snapshot only.
 * Satisfies the `EffectiveSetResolver` port structurally (domain imports no port).
 */
export class DomainEffectiveSetResolver {
  private readonly cap: number;

  constructor(private readonly options: EffectiveSetOptions) {
    this.cap = options.cap ?? DEFAULT_EFFECTIVE_SET_CAP;
  }

  resolve(
    subject: PinSubject,
    config: EffectiveRunConfig,
    snapshot: Snapshot,
  ): EffectiveSet | Problem {
    const ceiling = config.dimensions[TOOLS_GRANT]?.value as
      ToolGrant | undefined;
    const effects = config.dimensions[TOOLS_EFFECTS]?.value as
      readonly ToolEffect[] | undefined;
    const items: EffectiveItem[] = [];
    const requiredMissing: EffectiveItem[] = [];
    for (const { entry, grantedBy } of grantedEntries(subject, snapshot)) {
      const inst = snapshot.installations.get(entry.installation);
      const unavailable = (
        reason: UnavailableReason,
        provenance: EffectiveItem['provenance'] = {
          grantedBy,
          visibleVia: null,
          approvedBy: null,
        },
      ): void => {
        const item: EffectiveItem = {
          installation: entry.installation,
          item: inst?.item ?? null,
          kind: inst?.kind ?? null,
          state: 'unavailable',
          reason,
          tools: [],
          binding: null,
          provenance,
        };
        items.push(item);
        if (entry.required) requiredMissing.push(item);
      };
      if (
        inst === undefined ||
        inst.status === 'removed' ||
        inst.status === 'draft'
      ) {
        unavailable('not_installed');
        continue;
      }
      if (inst.status === 'disabled') {
        unavailable('disabled');
        continue;
      }
      if (config.dimensions[kindAllowed(inst.kind)]?.value === false) {
        unavailable('policy_ceiling');
        continue;
      }
      const via = visibleVia(inst, subject, snapshot, grantedBy);
      if (via === null) {
        unavailable('not_visible');
        continue;
      }
      const provenance = {
        grantedBy,
        visibleVia: via,
        approvedBy: inst.approved?.approvalRef ?? null,
      };
      const approved = inst.approved;
      if (approved === null || inst.status === 'pending_approval') {
        unavailable('not_approved', provenance);
        continue;
      }
      const allowed = ceiling?.[inst.id];
      const effectOf = snapshot.toolEffects.get(inst.id);
      const names = Object.keys(approved.tools)
        .filter((tool) => entry.tools === null || entry.tools.includes(tool))
        .filter(
          (tool) =>
            ceiling === undefined ||
            (allowed !== undefined &&
              (allowed === null || allowed.includes(tool))),
        )
        .filter((tool) => {
          if (effects === undefined) return true;
          const effect = effectOf?.get(tool) ?? null;
          return effect !== null && effects.includes(effect);
        })
        .sort();
      if (names.length === 0) {
        unavailable('not_granted', provenance);
        continue;
      }
      const credential = bindingFor(inst, subject, snapshot.bindings);
      if ('unavailable' in credential) {
        unavailable(credential.unavailable, provenance);
        continue;
      }
      const binding = credential.binding;
      items.push({
        installation: inst.id,
        item: inst.item,
        kind: inst.kind,
        state: 'ready',
        reason: null,
        tools: names.flatMap((tool) => {
          const descriptorDigest = approved.tools[tool];
          return descriptorDigest === undefined
            ? []
            : [{ toolId: toolIdOf(inst, tool), descriptorDigest }];
        }),
        binding:
          binding === null || binding.secret === null
            ? null
            : {
                ref: binding.id,
                owner: binding.owner,
                versionDigest: binding.secret.versionDigest,
              },
        provenance,
      });
    }
    return (
      this.refusal(config, items, requiredMissing) ?? {
        items,
        digest: this.options.digest(items),
      }
    );
  }

  private refusal(
    config: EffectiveRunConfig,
    items: readonly EffectiveItem[],
    requiredMissing: readonly EffectiveItem[],
  ): Problem | null {
    const tools = items.reduce((sum, item) => sum + item.tools.length, 0);
    if (tools > this.cap) {
      return problem(
        'CP_EFFECTIVE_SET_TOO_LARGE',
        `This run would get ${String(tools)} tools; the limit is ${String(this.cap)}.`,
        { action: 'narrow_grant' },
      );
    }
    const required = requiredMissing[0];
    if (required !== undefined) {
      return problem(
        REASON_CODES[required.reason ?? 'not_installed'],
        `A required item is unavailable (${String(required.reason)}).`,
        { subject: { installation: required.installation } },
      );
    }
    const asked = config.dimensions[TOOLS_GRANT];
    if (asked?.setBy === 'request') {
      const [first, ...rest] = Object.keys(asked.value as ToolGrant);
      const ready = new Set(
        items
          .filter((item) => item.state === 'ready')
          .map((item) => item.installation),
      );
      if (
        first !== undefined &&
        ![first, ...rest].some((inst) => ready.has(inst))
      ) {
        return problem(
          'CP_NOT_GRANTED',
          'None of the tools this request asked for are available.',
          { subject: { installation: first } },
        );
      }
    }
    return null;
  }
}
