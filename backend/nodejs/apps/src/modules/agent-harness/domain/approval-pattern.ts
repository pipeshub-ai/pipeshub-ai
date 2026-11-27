import { createHash } from 'node:crypto';
import { canonicalJson } from '../contracts/canonical-json';

/** GA-08 decides 20 at once; the queue lists at most 100, so a batch never needs more than half a page. */
export const APPROVAL_BATCH_MAX_ITEMS = 50;

/** What a pattern is read from: the call as its approval card shows it. */
export interface PatternSubject {
  toolName: string;
  /** The call's arguments as journaled JSON text. */
  args: string;
  reason?: {
    rule: string;
    sensitivity?: 'public' | 'private' | 'unknown';
    destination?: string;
    readonly [editionField: string]: unknown;
  };
}

/**
 * Why a held call is decided only on its own card, never in a batch across
 * sessions (G-13, GA-08). A batch is for repeated low-risk asks of a layer's
 * own write rules; a call that would send what the run read, or that an
 * edition's rule decides, is weighed one at a time.
 */
export type BatchExclusion =
  | 'unexplained'
  | 'external_policy'
  | 'data_flow'
  | 'edition_rule'
  | 'read_context';

/** The org's policy webhook asked (G-40): its own system's ask, weighed one call at a time. */
export const EXTERNAL_POLICY_RULE = 'external_policy';

/** The layer rules a batch may decide: an approval rule that asks before a kind of call, not before a data flow. */
const BATCHABLE_RULES: ReadonlySet<string> = new Set([
  'writes',
  'effect_class',
  'tool',
  'engine_writes',
]);

// The reason fields a pattern reads; any other is an edition rule's (`editionReasonFields`).
const SUBJECT_FIELDS: ReadonlySet<string> = new Set([
  'interactionId',
  'rule',
  'layer',
  'toolId',
  'sensitivity',
  'sources',
  'moreSources',
  'destination',
  'argsDigest',
  'conversationGrant',
  'editable',
  'fieldOrder',
  'ruleGrant',
]);

export function batchExclusion(subject: PatternSubject): BatchExclusion | null {
  const reason = subject.reason;
  // A reason written before layers explained themselves cannot be told apart from a data-flow ask.
  if (reason === undefined) return 'unexplained';
  if (reason.rule === EXTERNAL_POLICY_RULE) return 'external_policy';
  if (Object.keys(reason).some((field) => !SUBJECT_FIELDS.has(field)))
    return 'edition_rule';
  if (!BATCHABLE_RULES.has(reason.rule)) return 'data_flow';
  if (reason.sensitivity === 'private' || reason.sensitivity === 'unknown')
    return 'read_context';
  return null;
}

/**
 * JSON text as a value, so key order and spacing never split one pattern into
 * two; text that is not canonical JSON (a lone surrogate, a reserved key) is
 * kept byte for byte.
 */
function patternText(
  toolName: string,
  args: string,
  destination: string | null,
): string {
  try {
    return canonicalJson({
      tool: toolName,
      args: { json: JSON.parse(args) as unknown },
      destination,
    });
  } catch {
    return JSON.stringify({
      tool: toolName,
      args: { text: args },
      destination,
    });
  }
}

/**
 * The pattern a batch decision is bound to: the tool, its arguments and where
 * the call sends them, digested. Two calls share it only when all three are
 * the same, so a batch never mixes different arguments.
 */
export function approvalPattern(subject: PatternSubject): string {
  const digest = createHash('sha256')
    .update(
      patternText(
        subject.toolName,
        subject.args,
        subject.reason?.destination ?? null,
      ),
    )
    .digest('hex');
  return `p1:${digest}`;
}
