import type { EffectiveItem, EffectiveSet, Installation } from './objects';

/** The `dimension` label of `pipeshub_cp_shadow_mismatch_total` (05 §1 rule 6). */
export type ShadowDimension =
  'toolset' | 'mcp' | 'skill' | 'visibility' | 'approval' | 'unavailable';

/** What today's resolver pinned for a run's catalog items: connector tool ids, MCP instances, skill names. */
export interface LegacyPin {
  /** Tool ids of instance-bound toolset tools (`<toolset>@<instanceId>/<tool>`); platform tools are not catalog items. */
  readonly toolsetTools: readonly string[];
  /** `mcp@<instanceId>/<tool>`. */
  readonly mcpTools: readonly string[];
  readonly skills: readonly string[];
}

/** Credentials are checked at call time today and bound by CPD-8, so they are no difference (D7). */
const CREDENTIAL_REASONS = new Set<EffectiveItem['reason']>([
  'credential_missing',
  'credential_expired',
  'credential_revoked',
]);

const kindOf = (item: EffectiveItem): 'toolset' | 'mcp' | 'skill' | null =>
  item.kind === 'toolset' || item.kind === 'mcp' || item.kind === 'skill'
    ? item.kind
    : null;

const instanceOfTool = (toolId: string): string =>
  toolId.slice(toolId.indexOf('@') + 1, toolId.lastIndexOf('/'));
const nameOfTool = (toolId: string): string =>
  toolId.slice(toolId.lastIndexOf('/') + 1);

/**
 * The dimensions on which the control plane's effective set and today's pin
 * disagree for one run. A legacy item the effective set lists as not visible
 * or not approved counts under `visibility` or `approval` (what CPD-7's
 * switch would change); any other difference counts under its kind. Tools are
 * compared by instance and name, so the two tool-id spellings may differ.
 */
export function shadowMismatches(
  legacy: LegacyPin,
  set: EffectiveSet,
  installations: ReadonlyMap<string, Installation>,
): ShadowDimension[] {
  const found = new Set<ShadowDimension>();
  const byInstallation = new Map(
    set.items.map((item) => [item.installation, item]),
  );
  const credentialOnly = (item: EffectiveItem | undefined): boolean =>
    item !== undefined && CREDENTIAL_REASONS.has(item.reason);
  const explained = (
    item: EffectiveItem | undefined,
  ): ShadowDimension | null =>
    item?.reason === 'not_visible'
      ? 'visibility'
      : item?.reason === 'not_approved'
        ? 'approval'
        : null;

  const compareTools = (
    dimension: 'toolset' | 'mcp',
    tools: readonly string[],
  ): void => {
    const pinned = new Set(
      tools.map((toolId) => `${instanceOfTool(toolId)}/${nameOfTool(toolId)}`),
    );
    const resolved = new Set(
      set.items
        .filter((item) => kindOf(item) === dimension && item.state === 'ready')
        .flatMap((item) =>
          item.tools.map(
            (tool) => `${item.installation}/${nameOfTool(tool.toolId)}`,
          ),
        ),
    );
    for (const key of pinned) {
      if (resolved.has(key)) continue;
      const item = byInstallation.get(key.slice(0, key.lastIndexOf('/')));
      if (credentialOnly(item)) continue;
      found.add(explained(item) ?? dimension);
    }
    for (const key of resolved) if (!pinned.has(key)) found.add(dimension);
  };
  compareTools('toolset', legacy.toolsetTools);
  compareTools('mcp', legacy.mcpTools);

  const skillName = (item: EffectiveItem): string | null => {
    const config = installations.get(item.installation)?.config as
      { name?: unknown } | undefined;
    return typeof config?.name === 'string' ? config.name : null;
  };
  const skills = set.items.filter((item) => kindOf(item) === 'skill');
  const readySkills = new Set(
    skills.filter((item) => item.state === 'ready').map(skillName),
  );
  for (const name of legacy.skills) {
    if (readySkills.has(name)) continue;
    const item = skills.find((candidate) => skillName(candidate) === name);
    if (!credentialOnly(item)) found.add(explained(item) ?? 'skill');
  }
  for (const name of readySkills)
    if (name === null || !legacy.skills.includes(name)) found.add('skill');
  return [...found].sort();
}
