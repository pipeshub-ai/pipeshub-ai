/**
 * A durable conversation with a run in flight, served from page.route() for the mocked harness specs.
 *
 * The page opens the conversation (`/chat/?conversationId=…`), reads its `activeRunId` and reattaches to the run's
 * stream with `Last-Event-ID`, as it does after a reload or from another tab. No spec here mocks how a new turn is
 * admitted (`POST …/runs` before LAT1, the chat stream route after it): sending a turn is the real-stack specs'
 * job (`tests/e2e/harness/`).
 */
import type { Page, Route } from '@playwright/test';

export const MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'quick',
  modelFriendlyName: 'GPT-4o mini',
};

export interface SseFrame {
  id: string;
  type: string;
  data: Record<string, unknown>;
}

export const sse = (frames: SseFrame[]): string =>
  frames.map((f) => `id: ${f.id}\nevent: ${f.type}\ndata: ${JSON.stringify({ type: f.type, ...f.data })}\n\n`).join('');

/** The frames after `Last-Event-ID`, up to what the test has released, as Node's projection serves them. */
export class FakeRunStream {
  released = 0;

  constructor(private readonly frames: () => SseFrame[]) {}

  releaseThrough(predicate: (frame: SseFrame) => boolean): void {
    this.released = this.frames().findIndex(predicate) + 1;
  }

  releaseAll(): void {
    this.released = Number.MAX_SAFE_INTEGER;
  }

  get finished(): boolean {
    return this.released >= this.frames().length;
  }

  async serve(route: Route): Promise<void> {
    const all = this.frames();
    const lastEventId = route.request().headers()['last-event-id'] ?? null;
    const from = lastEventId === null ? 0 : all.findIndex((f) => f.id === lastEventId) + 1;
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: sse(all.slice(from, this.released)),
    });
  }
}

export const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export interface InFlightConversation {
  sessionId: string;
  runId: string;
  stream: FakeRunStream;
  /** The conversation as Node stores it at this moment (the answer only once the run finished). */
  stored: () => Record<string, unknown>;
  /** The viewer's access; default the owner. */
  access?: { isOwner: boolean; accessLevel: string };
}

/** The page's reads of a chat conversation whose durable run is still going. */
export async function mockInFlightConversation(page: Page, c: InFlightConversation): Promise<void> {
  // The page resumes a run only once it knows the flags; on a slow (dev) server the conversation could arrive
  // first and the run was never reattached. The conversation is served after this page load's flags.
  let flagsServed = false;
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) flagsServed = false;
  });
  const afterFlags = async () => {
    for (let i = 0; i < 200 && !flagsServed; i += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  };
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', async (route) => {
    await json(route, {
      featureFlags: {},
      agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
    });
    flagsServed = true;
  });
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, {
      status: 'success',
      message: 'Success',
      models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
    }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? json(route, {
          conversations: [],
          source: 'owned',
          pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
        })
      : route.fallback(),
  );
  const base = `**/api/v1/conversations/${c.sessionId}`;
  await page.route(`${base}/runs/${c.runId}/stream`, (route) => c.stream.serve(route));
  await page.route(`${base}/runs/${c.runId}`, (route) =>
    json(route, { run: { runId: c.runId, status: c.stream.finished ? 'completed' : 'running', revision: 3 } }),
  );
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${c.sessionId}`,
    async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      await afterFlags();
      return json(route, {
        conversation: {
          ...c.stored(),
          id: c.sessionId,
          access: c.access ?? { isOwner: true, accessLevel: 'OWNER' },
          activeRunId: c.stream.finished ? null : c.runId,
        },
        filters: {},
        meta: {},
      });
    },
  );
}

export const openConversation = (page: Page, sessionId: string) => page.goto(`/chat/?conversationId=${sessionId}`);
