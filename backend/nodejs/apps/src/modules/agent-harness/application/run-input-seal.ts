import { HarnessUnavailableError } from '../domain/errors';
import type { SealContext, SealedContent } from '../domain/data-protection';
import type { HarnessRun, RunInputSnapshot } from '../domain/execution';
import { EnvelopeCipher } from './envelope-cipher';
import { journalContentFailure } from './sealed-journal-store';

/**
 * The input fields sealed together as JSON, by part: the person's content
 * besides the query (GAPS-1), the history admission read when it is kept
 * in the run document rather than by reference (GAPS2-1), and a job's task in
 * its parts (BGUI-10). Each later one is its own part so a row sealed before it
 * is never re-opened to add it.
 */
const FIELD_PARTS = {
  details: ['attachments', 'filters', 'appliedFilters', 'caller'],
  history: ['previousConversations'],
  task: ['task'],
} as const;
type FieldPart = keyof typeof FIELD_PARTS;
type Part = 'query' | FieldPart;
type Detail = (typeof FIELD_PARTS)[FieldPart][number];
/**
 * Left as they are while empty: a conversation's first turn has no history,
 * and an empty list says nothing a sealed one would hide, so a new
 * conversation's admission pays no seal for it.
 */
export const PLAIN_WHILE_EMPTY: ReadonlySet<string> = new Set<Detail>([
  'previousConversations',
]);
const toSeal = (field: Detail, value: unknown): boolean =>
  value !== undefined &&
  !(PLAIN_WHILE_EMPTY.has(field) && Array.isArray(value) && value.length === 0);
const SEALED = {
  query: 'querySealed',
  details: 'detailsSealed',
  history: 'historySealed',
  task: 'taskSealed',
} as const satisfies Record<Part, string>;
type SealedField = (typeof SEALED)[Part];
const FIELD_PART_NAMES = Object.keys(FIELD_PARTS) as FieldPart[];

/** Every input field once stored in plaintext: what the boot migration looks for. */
export const PLAINTEXT_INPUT_FIELDS: ReadonlyArray<'query' | Detail> = [
  'query',
  ...FIELD_PART_NAMES.flatMap((part) => FIELD_PARTS[part]),
];
/** The input fields that hold sealed content. */
export const SEALED_INPUT_FIELDS: readonly SealedField[] =
  Object.values(SEALED);

/** A run input as the run document, its receipts and a schedule store it: the person's content sealed. */
export type StoredQueryInput = Omit<RunInputSnapshot, 'query' | Detail> &
  Partial<Pick<RunInputSnapshot, 'query' | Detail>> &
  Partial<Record<SealedField, SealedContent>>;

type RunWithInput = Pick<HarnessRun, 'orgId' | 'runId' | 'input'>;

const sealedPart = (context: SealContext, part: Part): SealContext =>
  part === 'query' ? context : { ...context, part };

const withoutSealedFields = (input: StoredQueryInput): StoredQueryInput =>
  Object.fromEntries(
    Object.entries(input).filter(
      ([key]) => !(SEALED_INPUT_FIELDS as readonly string[]).includes(key),
    ),
  ) as StoredQueryInput;

/**
 * The person's content in the harness's own copies at rest (LCFIX, GAPS-1,
 * GAPS2-1): `agentRuns.input`, the receipts that copy the run and a
 * schedule's template keep the query, the attachments, the filters, the
 * caller and an inline history sealed with the org's data key, bound to their
 * run (or schedule) and part, as the journal keeps its own copy. Main's
 * `chatSessionMessages.content` stays plaintext by its own design: the
 * conversation module owns it and searches it. A run read from the store
 * carries the sealed fields; `open` gives the input back where its content is
 * needed (the worker's claim and spec pin, a stream's goal, an admission
 * replay, a schedule's firing).
 */
export class RunInputSeal {
  constructor(private readonly cipher: EnvelopeCipher | null) {}

  /** `run` with its content sealed; as it is without a cipher, without an input or once sealed. */
  async seal<R extends RunWithInput>(run: R): Promise<R> {
    const input = run.input as StoredQueryInput | null | undefined;
    if (!input) return run;
    const sealed = await this.sealInput(run.orgId, { runId: run.runId }, input);
    return sealed === input ? run : { ...run, input: sealed };
  }

  /** `run` with its content in plaintext; a run stored before sealing reads as it is. */
  async open<R extends RunWithInput>(run: R): Promise<R> {
    const input = run.input as StoredQueryInput | null | undefined;
    if (!input) return run;
    const opened = await this.openInput(run.orgId, { runId: run.runId }, input);
    return opened === input ? run : { ...run, input: opened };
  }

  /** `input` with its plaintext content sealed, bound to `context`; the same object when nothing is left to seal. */
  async sealInput(
    orgId: string,
    context: SealContext,
    input: StoredQueryInput,
  ): Promise<StoredQueryInput> {
    const cipher = this.cipher;
    if (cipher === null) return input;
    const query = typeof input.query === 'string' ? input.query : undefined;
    const parts = FIELD_PART_NAMES.map(
      (part) =>
        [
          part,
          FIELD_PARTS[part].filter((field) => toSeal(field, input[field])),
        ] as const,
    ).filter(([, present]) => present.length > 0);
    if (query === undefined && parts.length === 0) return input;
    const seal = (part: Part, plaintext: string): Promise<SealedContent> =>
      cipher.seal(
        orgId,
        'run.input',
        sealedPart(context, part),
        Buffer.from(plaintext, 'utf8'),
      );
    const out: StoredQueryInput = { ...input };
    if (query !== undefined) {
      delete out.query;
      out.querySealed = await seal('query', query);
    }
    for (const [part, present] of parts) {
      const fields: Record<string, unknown> = {};
      for (const field of present) {
        fields[field] = input[field];
        Reflect.deleteProperty(out, field);
      }
      out[SEALED[part]] = await seal(part, JSON.stringify(fields));
    }
    return out;
  }

  /** `input` with its content in plaintext; the same object when nothing of it is sealed. */
  async openInput(
    orgId: string,
    context: SealContext,
    input: StoredQueryInput,
  ): Promise<RunInputSnapshot> {
    if (!SEALED_INPUT_FIELDS.some((field) => input[field] !== undefined)) {
      return input as RunInputSnapshot;
    }
    const cipher = this.cipher;
    if (cipher === null) {
      throw new HarnessUnavailableError(
        'The run input is sealed and no data key is configured',
      );
    }
    const ref = `${Object.values(context).join(':')}:input`;
    const open = async (sealed: SealedContent, part: Part): Promise<string> => {
      try {
        return (
          await cipher.open(
            orgId,
            'run.input',
            sealedPart(context, part),
            sealed,
          )
        ).toString('utf8');
      } catch (error) {
        throw journalContentFailure(error, `run:${ref}`);
      }
    };
    const out = withoutSealedFields(input) as Record<string, unknown>;
    if (input.querySealed !== undefined) {
      out['query'] = await open(input.querySealed, 'query');
    }
    for (const part of FIELD_PART_NAMES) {
      const sealed = input[SEALED[part]];
      if (sealed === undefined) continue;
      Object.assign(
        out,
        JSON.parse(await open(sealed, part)) as Record<string, unknown>,
      );
    }
    return out as unknown as RunInputSnapshot;
  }

  /**
   * A secret of a schedule at rest (a signed webhook's key, TR-04), sealed as
   * its template is, bound to the schedule and to `part`; as it is without a
   * cipher, as the template is.
   */
  async sealSecret(
    orgId: string,
    context: SealContext,
    part: string,
    secret: string,
  ): Promise<SealedContent | string> {
    const cipher = this.cipher;
    if (cipher === null) return secret;
    return cipher.seal(
      orgId,
      'run.input',
      { ...context, part },
      Buffer.from(secret, 'utf8'),
    );
  }

  async openSecret(
    orgId: string,
    context: SealContext,
    part: string,
    stored: SealedContent | string,
  ): Promise<string> {
    if (typeof stored === 'string') return stored;
    const cipher = this.cipher;
    if (cipher === null) {
      throw new HarnessUnavailableError(
        'The secret is sealed and no data key is configured',
      );
    }
    try {
      return (
        await cipher.open(orgId, 'run.input', { ...context, part }, stored)
      ).toString('utf8');
    } catch (error) {
      throw journalContentFailure(
        error,
        `${Object.values(context).join(':')}:${part}`,
      );
    }
  }

  /** `run` without the sealed content: for a response whose reader never opens it (a worker's renew or release). */
  static withoutSealed<R extends RunWithInput>(run: R): R {
    const input = run.input as StoredQueryInput | null | undefined;
    if (!input) return run;
    return { ...run, input: withoutSealedFields(input) as RunInputSnapshot };
  }
}

/** Passes runs through as they are: stores and services built without a data key. */
export const UNSEALED_RUN_INPUT = new RunInputSeal(null);
