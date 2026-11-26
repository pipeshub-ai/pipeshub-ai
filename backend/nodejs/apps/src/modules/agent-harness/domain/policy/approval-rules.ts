/**
 * The `approval-rules` order (R2 CP3): per-call approval rules as one policy
 * dimension. A layer's rules (`approval-rules.schema.json`) lift to a ceiling
 * `{classes, tools, flow?}`; two ceilings meet as the strictest of them per
 * class, per tool and per flow rule. Python's `harness_core.policy.orders`
 * implements the same order; `contracts/harness/vectors/approvals.json` holds
 * both to one answer.
 */
import {
  approvalRulesSchema,
  type ApprovalRules,
} from '../../contracts/generated/core/approval-rules';
import {
  EFFECT_CLASSES,
  type ApprovalLevel,
  type Effect,
  type FlowLevel,
  type FlowThreshold,
} from '../../contracts/generated/core/enums';

// The code generators drop `maxProperties` and `propertyNames`; both readers apply them here.
export const MAX_TOOL_RULES = 500;
export const MAX_TOOL_ID_LENGTH = 256;
/** Canonical JSON refuses it as a key (both languages); no map key of the document may be it. */
export const RESERVED_KEY = '__proto__';

const RANK: Readonly<Record<ApprovalLevel, number>> = {
  allow: 0,
  ask: 1,
  deny: 2,
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export const stricter = (a: ApprovalLevel, b: ApprovalLevel): ApprovalLevel =>
  RANK[a] >= RANK[b] ? a : b;

export const atLeastAsStrict = (a: ApprovalLevel, b: ApprovalLevel): boolean =>
  RANK[a] >= RANK[b];

/** A layer's flow rule (SEC1's guard, per org since SEC2), `above` defaulted. */
export interface LayerFlow {
  level: FlowLevel;
  above: FlowThreshold;
}

/** A rule that narrows: `off` never is one. */
export interface FlowRule {
  level: 'ask' | 'deny';
  above: FlowThreshold;
}

/** One layer as both languages decide with it: `writes` folded into write and execute. */
export interface NormalizedRules {
  classes: Partial<Record<Effect, ApprovalLevel>>;
  tools: Record<string, ApprovalLevel>;
  flow?: LayerFlow;
}

export const WRITES_ASK: NormalizedRules = {
  classes: { write: 'ask', execute: 'ask' },
  tools: {},
};

/** The rule as `approval-rules.schema.json` reads it, normalized; null when refused. */
export function normalizeRules(raw: unknown): NormalizedRules | null {
  // zod copies a record by assignment, which drops an own `__proto__` key: check the input itself.
  const rawTools = isRecord(raw) ? raw['tools'] : undefined;
  if (
    isRecord(rawTools) &&
    Object.prototype.hasOwnProperty.call(rawTools, RESERVED_KEY)
  )
    return null;
  const parsed = approvalRulesSchema.safeParse(raw);
  if (!parsed.success) return null;
  const rule: ApprovalRules = parsed.data;
  const tools = Object.entries(rule.tools ?? {});
  if (
    tools.length > MAX_TOOL_RULES ||
    tools.some(
      ([id]) =>
        id.length < 1 || id.length > MAX_TOOL_ID_LENGTH || id === RESERVED_KEY,
    )
  ) {
    return null;
  }
  const classes: NormalizedRules['classes'] = {};
  if (rule.writes !== undefined) {
    classes.write = rule.writes;
    classes.execute = rule.writes;
  }
  for (const kind of EFFECT_CLASSES) {
    const level = rule.classes?.[kind];
    if (level !== undefined)
      classes[kind] = stricter(classes[kind] ?? 'allow', level);
  }
  const normalized: NormalizedRules = {
    classes,
    tools: Object.fromEntries(tools),
  };
  if (rule.flow !== undefined) {
    normalized.flow = {
      level: rule.flow.level,
      above: rule.flow.above ?? 'public',
    };
  }
  return normalized;
}

const FLOW_LEVEL_RANK: Readonly<Record<FlowRule['level'], number>> = {
  ask: 1,
  deny: 2,
};
const THRESHOLD_RANK: Readonly<Record<FlowThreshold, number>> = {
  public: 0,
  private: 1,
};

const covers = (a: FlowRule, b: FlowRule): boolean =>
  FLOW_LEVEL_RANK[a.level] >= FLOW_LEVEL_RANK[b.level] &&
  THRESHOLD_RANK[a.above] <= THRESHOLD_RANK[b.above];

/**
 * Python's `strictest_flows`: the rules that decide as the strictest of all of
 * them. A rule another covers (as strict, from as low a threshold) is dropped;
 * `ask` from a lower threshold and `deny` from a higher one can both remain.
 */
export function strictestFlows(
  flows: readonly (LayerFlow | FlowRule)[],
): FlowRule[] {
  const given: FlowRule[] = [];
  for (const flow of flows) {
    if (flow.level === 'off') continue;
    const rule: FlowRule = { level: flow.level, above: flow.above };
    if (!given.some((g) => g.level === rule.level && g.above === rule.above))
      given.push(rule);
  }
  return given
    .filter(
      (rule) =>
        !given.some(
          (other) =>
            (other.level !== rule.level || other.above !== rule.above) &&
            covers(other, rule),
        ),
    )
    .sort(
      (a, b) =>
        THRESHOLD_RANK[a.above] - THRESHOLD_RANK[b.above] ||
        FLOW_LEVEL_RANK[a.level] - FLOW_LEVEL_RANK[b.level],
    );
}

/** The order's value: per class the decision, per tool its rule, and the flow rules when any narrows. */
export interface EffectiveApprovals {
  classes: Record<Effect, ApprovalLevel>;
  tools: Record<string, ApprovalLevel>;
  /** Present only when a layer has a flow rule; the deployment's own rule joins in the worker. */
  flow?: FlowRule[];
}

/** A normalized layer as a ceiling of its own. */
export function approvalCeiling(rules: NormalizedRules): EffectiveApprovals {
  const classes = Object.fromEntries(
    EFFECT_CLASSES.map((kind) => [kind, rules.classes[kind] ?? 'allow']),
  ) as Record<Effect, ApprovalLevel>;
  const ceiling: EffectiveApprovals = { classes, tools: { ...rules.tools } };
  const flows = rules.flow === undefined ? [] : strictestFlows([rules.flow]);
  if (flows.length > 0) ceiling.flow = flows;
  return ceiling;
}

/**
 * A value already in the order's terms, read back as a ceiling: a pin's
 * `approvals`, which a child's `parent` layer carries as it was resolved.
 * Told from a layer's rules by `flow` being the list of rules (a layer has
 * one rule object; two rules that neither covers cannot be written as one).
 * Held to the same schema, member by member, so it is never read looser
 * than it says; null when it is not such a value or a member is refused.
 */
export function resolvedCeiling(raw: unknown): EffectiveApprovals | null {
  if (!isRecord(raw) || !Array.isArray(raw['flow'])) return null;
  const { flow, ...rules } = raw;
  const base = normalizeRules(rules);
  if (base === null) return null;
  let ceiling = approvalCeiling(base);
  for (const rule of flow as unknown[]) {
    const one = normalizeRules({ flow: rule });
    if (one?.flow === undefined) return null;
    ceiling = meetApprovals(ceiling, approvalCeiling(one));
  }
  return ceiling;
}

/** The strictest of two ceilings: per class, per tool, and the flow rules of both. */
export function meetApprovals(
  a: EffectiveApprovals,
  b: EffectiveApprovals,
): EffectiveApprovals {
  const classes = Object.fromEntries(
    EFFECT_CLASSES.map((kind) => [
      kind,
      stricter(a.classes[kind], b.classes[kind]),
    ]),
  ) as Record<Effect, ApprovalLevel>;
  const tools: Record<string, ApprovalLevel> = { ...a.tools };
  for (const [tool, level] of Object.entries(b.tools)) {
    tools[tool] = stricter(tools[tool] ?? 'allow', level);
  }
  const met: EffectiveApprovals = { classes, tools };
  const flows = strictestFlows([...(a.flow ?? []), ...(b.flow ?? [])]);
  if (flows.length > 0) met.flow = flows;
  return met;
}

/** `a` decides every class and tool at least as strictly as `b`, and some rule of `a` covers each of `b`'s. */
export function approvalsWithin(
  a: EffectiveApprovals,
  b: EffectiveApprovals,
): boolean {
  return (
    EFFECT_CLASSES.every((kind) =>
      atLeastAsStrict(a.classes[kind], b.classes[kind]),
    ) &&
    Object.entries(b.tools).every(([tool, level]) =>
      atLeastAsStrict(a.tools[tool] ?? 'allow', level),
    ) &&
    (b.flow ?? []).every((rule) =>
      (a.flow ?? []).some((own) => covers(own, rule)),
    )
  );
}
