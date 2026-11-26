/**
 * The approvals document `/services/agentHarness/approvals` (P2.6c), read by
 * one rule in Node and Python (`contracts/harness/vectors/approvals.json`):
 * a document whose top level the schema refuses is invalid as a whole (the
 * worker asks for writes); an entry the schema refuses fails closed for that
 * entry only. Its rule layers are the operator's `default` and `agents`. Any
 * other top-level map is one an edition's schema overlay adds: kept as stored
 * here, read through the edition's section or its own rule layers
 * (`OrgApprovalRules`). Pure; the stores and routes apply it.
 */
import type { ApprovalRules } from '../contracts/generated/core/approval-rules';
import {
  EFFECT_CLASSES,
  type ApprovalLevel,
  type Effect,
  type FlowLevel,
  type FlowThreshold,
} from '../contracts/generated/core/enums';
import { pipeshubApprovalsDocumentSchema } from '../contracts/generated/ext/pipeshub/approvals-document';
import {
  normalizeRules,
  WRITES_ASK,
  type NormalizedRules,
} from './policy/approval-rules';

export {
  MAX_TOOL_ID_LENGTH,
  MAX_TOOL_RULES,
  normalizeRules,
  RESERVED_KEY,
  strictestFlows,
  stricter,
  WRITES_ASK,
} from './policy/approval-rules';
export type {
  EffectiveApprovals,
  FlowRule,
  LayerFlow,
  NormalizedRules,
} from './policy/approval-rules';
export type { ApprovalLevel, FlowThreshold };
export type EffectClass = Effect;
export { EFFECT_CLASSES };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export type FlowSetting = FlowLevel;

/** Whether a rule is one the writer may store (the schema plus the caps the generators drop). */
export const isApprovalRules = (raw: unknown): raw is ApprovalRules =>
  normalizeRules(raw) !== null;

const RULE_MAPS = ['agents'] as const;
type RuleMap = (typeof RULE_MAPS)[number];

/** The top-level keys the schema holds: this repository's, and any an edition's overlay adds (x-extensible). */
export const APPROVALS_DOCUMENT_KEYS: ReadonlySet<string> = new Set(
  Object.keys(pipeshubApprovalsDocumentSchema.shape),
);

/** The document's top level once the schema admits it; entries still unparsed. */
export interface ApprovalsSections {
  default?: unknown;
  agents: Record<string, unknown>;
  /** The maps an edition's overlay adds (orgId -> entry), by top-level key, as stored. */
  editions: Record<string, Record<string, unknown>>;
}

export type ApprovalsDocumentRead =
  | { status: 'absent' }
  | { status: 'invalid' }
  | { status: 'valid'; sections: ApprovalsSections };

export function readApprovalsDocument(raw: unknown): ApprovalsDocumentRead {
  if (raw === null || raw === undefined) return { status: 'absent' };
  if (!isRecord(raw)) return { status: 'invalid' };
  const keys = Object.keys(raw);
  if (keys.some((key) => !APPROVALS_DOCUMENT_KEYS.has(key)))
    return { status: 'invalid' };
  const maps = keys.filter((key) => key !== 'default');
  if (maps.some((key) => !isRecord(raw[key]))) return { status: 'invalid' };
  const map = (key: string): Record<string, unknown> =>
    (raw[key] as Record<string, unknown> | undefined) ?? {};
  const ruleMaps: readonly string[] = RULE_MAPS;
  return {
    status: 'valid',
    sections: {
      ...('default' in raw ? { default: raw['default'] } : {}),
      ...(Object.fromEntries(RULE_MAPS.map((key) => [key, map(key)])) as Record<
        RuleMap,
        Record<string, unknown>
      >),
      editions: Object.fromEntries(
        maps
          .filter((key) => !ruleMaps.includes(key))
          .map((key) => [key, map(key)]),
      ),
    },
  };
}

const layer = (raw: unknown): NormalizedRules =>
  raw === undefined || raw === null
    ? { classes: {}, tools: {} }
    : (normalizeRules(raw) ?? WRITES_ASK);

/**
 * Rule layers an edition keeps in the approvals document beside the deployment's (`HarnessEdition.orgApprovalRules`),
 * as Python's `OrgApprovalRules`: `org` after `default`, `agent` after `agents[agentKey]`. Each entry is raw: a
 * layer that is not rules asks for writes.
 */
export interface OrgApprovalRules {
  org(sections: ApprovalsSections, orgId: string): readonly unknown[];
  agent(
    sections: ApprovalsSections,
    orgId: string,
    agentKey: string,
  ): readonly unknown[];
}

/** The layers a run of (org, agent) narrows through, in Python's order. */
export function approvalLayers(
  read: ApprovalsDocumentRead,
  orgId: string,
  agentKey: string | null,
  edition: OrgApprovalRules | null = null,
): NormalizedRules[] {
  if (read.status === 'absent') return [];
  if (read.status === 'invalid') return [WRITES_ASK];
  const { sections } = read;
  const layers = [
    layer(sections.default),
    ...(edition?.org(sections, orgId) ?? []).map(layer),
  ];
  if (agentKey !== null) {
    layers.push(layer(sections.agents[agentKey]));
    layers.push(
      ...(edition?.agent(sections, orgId, agentKey) ?? []).map(layer),
    );
  }
  return layers;
}
