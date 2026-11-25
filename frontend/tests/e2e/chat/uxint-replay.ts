/**
 * UXINT replays shared by the mocked interaction specs: the owner's "7 wonders + pptx" run (0ba643e6, 2026-10-18)
 * as the frames the projector now sends (each approval with its journal `seq` and batch `afterTurns`, then the
 * open count), released batch by batch as the loop resumes when every card of a batch is decided.
 */
import type { Page } from '@playwright/test';
import { buildAguiConversation } from './agui-sse-builder';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, type SseFrame } from './durable-conversation.mock';

/**
 * Evidence runs only (UXINT_SHOTS): the icon font comes from Google Fonts, and a browser in a container sometimes
 * loses that request (ERR_NETWORK_CHANGED); the test runner fetches it instead, with retries, so icons render.
 */
export async function evidenceFonts(page: Page): Promise<void> {
  if (!process.env.UXINT_SHOTS) return;
  await page.route(/fonts\.(googleapis|gstatic)\.com/, async (route) => {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        return await route.fulfill({ response: await route.fetch() });
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    return route.abort();
  });
}

export const WONDERS = ['Great Wall of China', 'Petra', 'Christ the Redeemer', 'Machu Picchu', 'Chichén Itzá', 'Colosseum', 'Taj Mahal'];
export const WONDERS_QUESTION = 'Can we create picture of all 7 wonders and then create pptx';
export const WONDERS_ANSWER = 'Here are the seven wonders and a 9-slide deck: seven-wonders.pptx.';

interface Call {
  id: string;
  tool: string;
  display: string;
  args: Record<string, unknown>;
  reason: Record<string, unknown>;
}
const writes = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };
const flow = { rule: 'flow_external_write', sensitivity: 'unknown', sources: [], moreSources: 0, destination: 'duckduckgo.com', conversationGrant: true };
const img = (t: string, i: number, w: string): Call => ({
  id: `${t}.${i}`,
  tool: 'image_generator__generate_image',
  display: 'Image Generator · generate_image',
  args: { prompt: `A photorealistic picture of ${w}`, size: '1024x1024' },
  reason: writes,
});
const search = (t: string, i: number, q: string): Call => ({ id: `${t}.${i}`, tool: 'web__web_search', display: 'Web · web_search', args: { query: q }, reason: flow });

/** The run's five batches as query.log records them: 1 image, 3 searches, 2 searches, 1 page, 7 images. */
export const BATCHES: Call[][] = [
  [img('t2', 0, 'the Seven Wonders of the World, collage')],
  [search('t4', 0, 'new seven wonders of the world list'), search('t4', 1, 'seven wonders photos public domain'), search('t4', 2, 'seven wonders facts')],
  [search('t5', 0, 'Chichen Itza facts'), search('t5', 1, 'Petra Jordan facts')],
  [{ id: 't6.0', tool: 'web__fetch_url', display: 'Web · fetch_url', args: { url: 'https://en.wikipedia.org/wiki/New7Wonders_of_the_World' }, reason: flow }],
  WONDERS.map((w, i) => img('t7', i, w)),
];
export const ASKS = BATCHES.flat().length;

/** The turn a batch's calls came from: interaction ids are `ix.approve.t{turn}.{i}`. */
const turnOf = (call: Call) => Number(call.id.slice(1, call.id.indexOf('.')));

export class WondersRun {
  readonly decisions: string[] = [];
  readonly stream: FakeRunStream;
  private readonly all: SseFrame[];

  constructor(
    readonly sessionId: string,
    readonly runId: string,
  ) {
    this.all = this.frames();
    this.stream = new FakeRunStream(() => this.all);
    this.stream.released = this.batchEnd(0);
  }

  private frames(): SseFrame[] {
    const run = this.runId;
    const out: [string, Record<string, unknown>][] = [['RUN_STARTED', { threadId: this.sessionId, runId: run }]];
    let seq = 10;
    const waiting = (count: number) => out.push(['CUSTOM', { name: 'interaction_waiting', value: { count, paused: false }, runId: run }]);
    BATCHES.forEach((batch, b) => {
      if (b > 0) {
        for (const c of BATCHES[b - 1]) {
          out.push(['CUSTOM', { name: 'approval_resolved', value: { interactionId: `ix.approve.${c.id}`, decision: 'approved' }, runId: run }]);
          out.push(['TOOL_CALL_END', { toolCallId: c.id, runId: run }]);
          out.push(['TOOL_CALL_RESULT', { toolCallId: c.id, messageId: `r-${c.id}`, content: 'ok', status: 'completed', runId: run }]);
        }
        waiting(0);
      }
      out.push(['TEXT_MESSAGE_START', { messageId: `m-${b}`, role: 'assistant', runId: run }]);
      out.push(['TEXT_MESSAGE_CONTENT', { messageId: `m-${b}`, delta: `Step ${b + 1}.`, runId: run }]);
      out.push(['TEXT_MESSAGE_END', { messageId: `m-${b}`, runId: run }]);
      for (const c of batch) {
        out.push(['TOOL_CALL_START', { toolCallId: c.id, toolCallName: c.tool, runId: run }]);
        out.push(['TOOL_CALL_ARGS', { toolCallId: c.id, delta: JSON.stringify(c.args), runId: run }]);
      }
      batch.forEach((c, i) => {
        seq += 1;
        out.push([
          'CUSTOM',
          {
            name: 'approval_requested',
            value: {
              interactionId: `ix.approve.${c.id}`,
              toolCallId: c.id,
              toolName: c.tool,
              displayName: c.display,
              args: JSON.stringify(c.args),
              argsSummary: null,
              reason: c.reason,
              seq,
              afterTurns: turnOf(c),
            },
            runId: run,
          },
        ]);
        waiting(i + 1);
      });
      out.push(['STATE_SNAPSHOT', { runId: run, snapshot: { batch: b } }]);
    });
    for (const c of BATCHES[BATCHES.length - 1]) {
      out.push(['CUSTOM', { name: 'approval_resolved', value: { interactionId: `ix.approve.${c.id}`, decision: 'approved' }, runId: run }]);
      out.push(['TOOL_CALL_END', { toolCallId: c.id, runId: run }]);
      out.push(['TOOL_CALL_RESULT', { toolCallId: c.id, messageId: `r-${c.id}`, content: 'ok', status: 'completed', runId: run }]);
    }
    waiting(0);
    out.push(['TEXT_MESSAGE_START', { messageId: 'm-final', role: 'assistant', runId: run }]);
    out.push(['TEXT_MESSAGE_CONTENT', { messageId: 'm-final', delta: WONDERS_ANSWER, runId: run }]);
    out.push(['TEXT_MESSAGE_END', { messageId: 'm-final', runId: run }]);
    out.push(['STATE_SNAPSHOT', { runId: run, snapshot: { final: true } }]);
    out.push(['RUN_FINISHED', { result: { conversation: this.stored(WONDERS_ANSWER), meta: { requestId: run, duration: 1 } } }]);
    return out.map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }));
  }

  /** The conversation as Node saves it: once answered, the reply's parts carry each call and its approval. */
  stored(answer?: string) {
    const conversation = buildAguiConversation({
      conversationId: this.sessionId,
      userMessageId: 'u1',
      question: WONDERS_QUESTION,
      modelInfo: MODEL_INFO,
      ...(answer ? { answer } : {}),
    });
    if (!answer) return conversation;
    let seq = 10;
    const parts = BATCHES.flatMap((batch, b) => [
      { type: 'text', content: `Step ${b + 1}.`, runId: this.runId },
      ...batch.map((c) => ({ type: 'tool_call', toolCallId: c.id, toolName: c.tool, displayName: c.display, status: 'completed', runId: this.runId })),
      ...batch.map((c) => {
        seq += 1;
        return { type: 'approval', interactionId: `ix.approve.${c.id}`, toolCallId: c.id, toolName: c.tool, displayName: c.display, args: JSON.stringify(c.args), status: 'approved', seq, afterTurns: turnOf(c), runId: this.runId };
      }),
    ]);
    parts.push({ type: 'text', content: answer, isFinal: true, runId: this.runId } as (typeof parts)[number]);
    return { ...conversation, messages: conversation.messages.map((m) => (m['messageType'] === 'bot_response' ? { ...m, parts } : m)) };
  }

  private batchEnd(b: number): number {
    return this.all.findIndex((f) => f.type === 'STATE_SNAPSHOT' && (f.data.snapshot as { batch?: number }).batch === b) + 1;
  }

  /** Mocks the conversation, its stream and the decision route: a batch's frames follow once all its cards are decided. */
  async mock(page: Page): Promise<void> {
    await mockInFlightConversation(page, {
      sessionId: this.sessionId,
      runId: this.runId,
      stream: this.stream,
      stored: () => this.stored(this.stream.finished ? WONDERS_ANSWER : undefined),
    });
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/interactions/authority`, (route) =>
      json(route, { approve: true, deny: true }),
    );
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/followups*`, (route) => json(route, { suggestions: [] }));
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/interactions/*/decision`, (route) => {
      const ix = decodeURIComponent(route.request().url().split('/interactions/')[1]?.split('/')[0] ?? '');
      this.decide([ix]);
      return json(route, { receipt: { commandId: `c-${ix}` }, interaction: { interactionId: ix, decision: 'approved' } });
    });
    // "Allow all N" (DECIDE409): the batch in one request.
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/interactions/decisions`, (route) => {
      const { interactionIds } = route.request().postDataJSON() as { interactionIds: string[] };
      this.batchRequests.push(interactionIds);
      this.decide(interactionIds);
      return json(route, {
        receipt: { commandId: `c-batch-${this.batchRequests.length}` },
        interactions: interactionIds.map((interactionId) => ({ interactionId, decision: 'approved' })),
      });
    });
  }

  /** Each "Allow all" request's interaction ids. */
  readonly batchRequests: string[][] = [];

  private decide(ids: readonly string[]): void {
    this.decisions.push(...ids);
    for (const ix of ids) {
      const b = BATCHES.findIndex((batch) => batch.some((c) => `ix.approve.${c.id}` === ix));
      if (b >= 0 && BATCHES[b].every((c) => this.decisions.includes(`ix.approve.${c.id}`))) {
        this.stream.released = b + 1 < BATCHES.length ? this.batchEnd(b + 1) : Number.MAX_SAFE_INTEGER;
      }
    }
  }
}
