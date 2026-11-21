import { ArtifactAccess } from './artifact-access';
import { CitationAccess } from './citation-access';
import { AguiFrame, AGUI, Audience } from './frames';

/**
 * What each audience other than the run's owner receives of the owner's
 * frames (REPORT 8, S-F17; P1b C-5, C-20). A surface gets exactly what its
 * legacy route rendered: less would change what its user sees, more would
 * expose what legacy never did.
 */

/** A frame type admitted whole, or reduced to these fields (`type` is always kept). */
type Fields = 'all' | readonly string[];

export interface AudienceView {
  frames: Readonly<Partial<Record<string, Fields>>>;
  /** CUSTOM frames admitted, by name. */
  custom: ReadonlySet<string>;
  /** Fields of a run-state snapshot taken mid-run; none: no such snapshot is admitted. */
  stepSnapshot: readonly string[];
  /** A surface the owner reads through keeps the owner's citations; a viewer's are re-checked (C-6). */
  citations: 'owner' | 'checked';
  /** Likewise for artifacts: a viewer sees only those whose sources they can read (SS0). */
  artifacts: 'owner' | 'checked';
  /** It sees the streamed answer, so it gets a failed model call's retraction (main #4442) too, as its bot reads it. */
  retractions: boolean;
  /**
   * It cannot show an approval card, so its stream ends at one with the conversation handed to PipesHub (ADR-OWD-09
   * D-MCP-ASK, D-SLACK-ASK): a reader that takes one stream to its end would otherwise wait for the person's decision.
   */
  handsOff: boolean;
}

export type NonOwnerAudience = Exclude<Audience, 'owner'>;

const ANSWER_FRAMES: AudienceView['frames'] = {
  [AGUI.CUSTOM]: 'all',
  [AGUI.RUN_STARTED]: 'all',
  [AGUI.RUN_ERROR]: 'all',
  [AGUI.RUN_FINISHED]: 'all',
  [AGUI.STATE_DELTA]: 'all',
  [AGUI.STATE_SNAPSHOT]: 'all',
};

export const AUDIENCE_VIEWS: Readonly<Record<NonOwnerAudience, AudienceView>> =
  {
    shared: {
      frames: ANSWER_FRAMES,
      // UXINT-5: a count only, so the viewer sees the run waits on its owner, never what for.
      custom: new Set(['conversation_created', 'interaction_waiting']),
      stepSnapshot: [],
      citations: 'checked',
      artifacts: 'checked',
      retractions: false,
      // A viewer of someone else's run reads on in the web app, which shows the wait (UXINT-5).
      handsOff: false,
    },
    // Legacy MCP's tool result is the finished conversation or the error; it never carried the question card.
    external: {
      frames: ANSWER_FRAMES,
      custom: new Set(['conversation_created']),
      stepSnapshot: [],
      citations: 'owner',
      artifacts: 'owner',
      retractions: false,
      handsOff: true,
    },
    // Exactly the fields the legacy bot reads (`integrations/slack-bot/src/utils/agui-stream.ts`):
    // its activity timeline (tool status, narration, reasoning) and the streamed answer.
    slack: {
      frames: {
        [AGUI.CUSTOM]: ['name', 'value'],
        [AGUI.RUN_STARTED]: ['runId'],
        [AGUI.RUN_ERROR]: ['message'],
        [AGUI.RUN_FINISHED]: ['result'],
        [AGUI.STATE_DELTA]: ['delta'],
        [AGUI.STATE_SNAPSHOT]: ['snapshot'],
        [AGUI.TEXT_MESSAGE_START]: ['runId'],
        [AGUI.TEXT_MESSAGE_CONTENT]: ['delta', 'runId'],
        [AGUI.TEXT_MESSAGE_END]: ['runId'],
        [AGUI.REASONING_MESSAGE_CONTENT]: ['delta', 'runId'],
        [AGUI.REASONING_END]: [],
        [AGUI.TOOL_CALL_START]: [
          'toolCallId',
          'toolCallName',
          'displayName',
          'runId',
        ],
        [AGUI.TOOL_CALL_ARGS]: ['toolCallId', 'delta'],
        [AGUI.TOOL_CALL_RESULT]: ['toolCallId', 'status', 'runId'],
      },
      custom: new Set([
        'conversation_created',
        'ask_user_question',
        'message_discarded',
      ]),
      stepSnapshot: ['status', 'current_tool'],
      citations: 'owner',
      artifacts: 'owner',
      retractions: true,
      handsOff: true,
    },
  };

/** How an audience sees citations. */
export type CitationView = Pick<CitationAccess, 'citations' | 'messages'>;

const OWNER_CITATIONS: CitationView = {
  citations: (citations) => citations,
  messages: (messages) => messages,
};

/** How an audience sees artifacts. */
export type ArtifactView = Pick<
  ArtifactAccess,
  'card' | 'text' | 'messages' | 'completion'
>;

const OWNER_ARTIFACTS: ArtifactView = {
  card: (card) => card,
  text: (text) => text,
  messages: (messages) => messages,
  completion: (completion) => completion,
};

/** What the projector knows that a frame alone does not say. */
export interface AudienceContext {
  /** A shared viewer's citation access (C-6). */
  checkedCitations(): CitationView;
  /** A shared viewer's artifact access (SS0). */
  checkedArtifacts(): ArtifactView;
  /** The call asks the user: its arguments are the question legacy showed. */
  asksUser(toolCallId: unknown): boolean;
}

const ANSWER_DELTA_PATHS = new Set([
  '/artifacts/-',
  '/citations',
  '/normalizedAnswer',
  '/rawLength',
  '/confidence',
]);

const DISCARDED_PATH = '/discarded';

/** Answer-path ops whose value a viewer's checks may change. */
const CHECKED_DELTA_PATHS = new Set([
  '/citations',
  '/artifacts/-',
  '/normalizedAnswer',
]);

type DeltaOp = { path?: string; value?: unknown };

function checkedOp(
  op: DeltaOp,
  citations: () => CitationView,
  artifacts: () => ArtifactView,
): DeltaOp {
  switch (op.path) {
    case '/citations':
      return { ...op, value: citations().citations(op.value) };
    case '/artifacts/-':
      return { ...op, value: artifacts().card(op.value) };
    case '/normalizedAnswer':
      return typeof op.value === 'string'
        ? { ...op, value: artifacts().text(op.value) }
        : op;
    case undefined:
    default:
      return op;
  }
}

/**
 * A draft card, or the update that removes a discarded one (P3.5, U1). Only the
 * owner follows a card from draft to final in place; anyone else gets the final
 * card once, so no surface shows a card that is later withdrawn.
 */
function provisionalCard(op: DeltaOp): boolean {
  if (op.path !== '/artifacts/-') return false;
  const value = op.value;
  if (value === null || typeof value !== 'object') return false;
  const lifecycle = (value as Record<string, unknown>)['lifecycle'];
  return lifecycle === 'draft' || lifecycle === 'discarded';
}

// The legacy Slack bot's rule for the question tool, whatever toolset prefix the model saw.
const QUESTION_TOOL = /(?:^|[._])ask_user_question$/i;

export const isQuestionTool = (name: string): boolean =>
  QUESTION_TOOL.test(name.trim());

const pick = (
  value: Record<string, unknown>,
  fields: readonly string[],
): Record<string, unknown> =>
  Object.fromEntries(
    fields
      .filter((field) => field in value)
      .map((field) => [field, value[field]]),
  );

/** The owner's frame as `audience` receives it, or null when it does not. */
export function frameFor(
  audience: NonOwnerAudience,
  frame: AguiFrame,
  context: AudienceContext,
): AguiFrame | null {
  const view = AUDIENCE_VIEWS[audience];
  const fields = view.frames[frame.type];
  if (fields === undefined) return null;
  const shaped = shape(view, frame, context);
  if (shaped === null || fields === 'all') return shaped;
  return { type: shaped.type, ...pick(shaped, fields) };
}

function shape(
  view: AudienceView,
  frame: AguiFrame,
  context: AudienceContext,
): AguiFrame | null {
  const citations = (): CitationView =>
    view.citations === 'owner' ? OWNER_CITATIONS : context.checkedCitations();
  const artifacts = (): ArtifactView =>
    view.artifacts === 'owner' ? OWNER_ARTIFACTS : context.checkedArtifacts();
  switch (frame.type) {
    case AGUI.CUSTOM:
      return view.custom.has(String(frame['name'])) ? frame : null;
    case AGUI.TOOL_CALL_ARGS:
      // Tool arguments reach no one but the owner, except the question put to the user.
      return context.asksUser(frame['toolCallId']) ? frame : null;
    case AGUI.RUN_FINISHED:
      return sharedRunFinished(frame, citations(), artifacts());
    case AGUI.STATE_DELTA: {
      const sent = Array.isArray(frame['delta'])
        ? (frame['delta'] as Array<{ path?: string; value?: unknown }>)
        : [];
      // The bot reads a retraction's answer reset, not its `/discarded` marker.
      const all = view.retractions
        ? sent.filter((op) => op.path !== DISCARDED_PATH)
        : sent;
      if (all.length < sent.length && all.length === 0) return null;
      if (!all.every((op) => ANSWER_DELTA_PATHS.has(op.path ?? ''))) {
        return null;
      }
      const ops = all.filter((op) => !provisionalCard(op));
      if (ops.length < all.length && ops.length === 0) return null;
      if (!ops.some((op) => CHECKED_DELTA_PATHS.has(op.path ?? ''))) {
        return ops.length < sent.length ? { ...frame, delta: ops } : frame;
      }
      return {
        ...frame,
        delta: ops.map((op) => checkedOp(op, citations, artifacts)),
      };
    }
    case AGUI.STATE_SNAPSHOT: {
      const snapshot = frame['snapshot'] as Record<string, unknown>;
      if (snapshot['final'] === true) {
        return {
          ...frame,
          snapshot: sharedCompletion(snapshot, citations(), artifacts()),
        };
      }
      return view.stepSnapshot.length === 0
        ? null
        : { ...frame, snapshot: pick(snapshot, view.stepSnapshot) };
    }
    default:
      return frame;
  }
}

/** What a shared-conversation viewer may see of a finished answer (S-F17, C-6). */
export function sharedCompletion(
  completion: Record<string, unknown>,
  access: CitationView,
  artifacts: ArtifactView,
): Record<string, unknown> {
  const parts = Array.isArray(completion['parts'])
    ? (completion['parts'] as Array<Record<string, unknown>>)
        .filter((part) => part['type'] === 'text' && part['isFinal'] === true)
        .map((part) =>
          'notFound' in part
            ? { ...part, notFound: sharedNotFound(part['notFound']) }
            : part,
        )
    : [];
  const kept: Record<string, unknown> = {};
  for (const key of [
    'final',
    'status',
    'answer',
    'citations',
    'confidence',
    'answerMatchType',
    'errorCode',
    'truncatedBy',
    'budgetScope',
    // R32REPO-FU-1: the repositories it names are the request's, and the answer's text starts with them anyway.
    'limits',
  ]) {
    if (key in completion) kept[key] = completion[key];
  }
  if ('notFound' in completion)
    kept['notFound'] = sharedNotFound(completion['notFound']);
  if ('citations' in kept)
    kept['citations'] = access.citations(kept['citations']);
  return artifacts.completion({ ...kept, parts });
}

/**
 * The not-found fact without the closest records' names (FU-2): they come from the owner's own search, which another
 * viewer may not be able to read.
 */
function sharedNotFound(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  const { near: _owners, ...kept } = value as Record<string, unknown>;
  return kept;
}

function sharedRunFinished(
  frame: AguiFrame,
  access: CitationView,
  artifacts: ArtifactView,
): AguiFrame {
  // A child's end carries no answer of its own: nothing to check, and no conversation to invent.
  if (!('result' in frame)) return frame;
  const result = (frame['result'] ?? {}) as Record<string, unknown>;
  const conversation = (result['conversation'] ?? {}) as Record<
    string,
    unknown
  >;
  const messages = Array.isArray(conversation['messages'])
    ? (conversation['messages'] as Array<Record<string, unknown>>).map(
        (message) => {
          const { reasoning: _reasoning, parts, ...rest } = message;
          return {
            ...rest,
            ...(Array.isArray(parts)
              ? {
                  parts: (parts as Array<Record<string, unknown>>).filter(
                    (part) =>
                      part['type'] === 'text' && part['isFinal'] === true,
                  ),
                }
              : {}),
          };
        },
      )
    : conversation['messages'];
  return {
    ...frame,
    result: {
      ...result,
      conversation: {
        ...conversation,
        messages: artifacts.messages(access.messages(messages)),
      },
    },
  };
}
