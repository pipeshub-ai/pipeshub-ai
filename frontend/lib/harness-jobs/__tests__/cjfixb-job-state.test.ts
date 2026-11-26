import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * CJFIX-B (ADR-CJ-01): what more than one place shows of a job lives in the jobs store, keyed by its run id. Its
 * timeline is read once while it runs and once after it ended, whoever asks (F8); a shared viewer never reads it
 * (F3); a Stop's outcome outlives the card that sent it (F9); and a status only moves forward (F6).
 */

type Handlers = { onEvent: (e: unknown) => void; onError: (e: Error) => void };
const streams = vi.hoisted(() => [] as Array<(h: Handlers) => void>);
vi.mock('@/lib/api', async (load) => ({
  ...(await load<typeof import('@/lib/api')>()),
  streamSSEGet: async (_url: string, h: Handlers) => (streams.shift() ?? (() => undefined))(h),
}));

const api = vi.hoisted(() => ({
  timeline: vi.fn(async () => ({ status: 'completed', operatorStop: null, codingEngine: 'opencode' })),
  stop: vi.fn(async () => false),
}));
vi.mock('../jobs-api', async (load) => {
  const real = await load<typeof import('../jobs-api')>();
  return { ...real, JobsApi: { ...real.JobsApi, timeline: api.timeline, stop: api.stop } };
});

const { followJob, loadJobTimeline, readSettled, stopJob, useJobsStore } = await import('../jobs-store');
const { reduceJobFrame, emptyJobStream } = await import('../job-stream');

const session = { sessionId: 'c1' };
const frame = (runId: string, type: string, id: string) => ({ id, data: { type, runId } });

beforeEach(() => {
  streams.length = 0;
  api.timeline.mockClear();
  api.stop.mockClear();
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
});

describe('F8: one timeline read per job once it ended', () => {
  it('the settle read, the panel and the steps share it; asking again reads nothing', async () => {
    await readSettled(session, 'j1');
    await Promise.all([loadJobTimeline(session, 'j1', { ended: true }), loadJobTimeline(session, 'j1', { ended: true })]);
    await loadJobTimeline(session, 'j1', { ended: false });
    expect(api.timeline).toHaveBeenCalledTimes(1);
    expect(useJobsStore.getState().extras.j1).toMatchObject({ settled: true, engine: 'opencode', operatorStop: null });
  });

  it('reads at once while it runs, once again after it ended, and twice at the same time is one read', async () => {
    api.timeline.mockResolvedValueOnce({ status: 'running', operatorStop: null, codingEngine: null });
    await Promise.all([loadJobTimeline(session, 'j2', { ended: false }), loadJobTimeline(session, 'j2', { ended: false })]);
    expect(api.timeline).toHaveBeenCalledTimes(1);
    await loadJobTimeline(session, 'j2', { ended: true });
    await loadJobTimeline(session, 'j2', { ended: true });
    expect(api.timeline).toHaveBeenCalledTimes(2);
  });

  it("a loop job's executor is no engine: its card goes on naming its model, never \"loop\"", async () => {
    api.timeline.mockResolvedValueOnce({ status: 'cancelled', operatorStop: null, codingEngine: null, executor: 'loop' } as never);
    await readSettled(session, 'j4');
    expect(useJobsStore.getState().extras.j4).toMatchObject({ settled: true, engine: null });
    api.timeline.mockResolvedValueOnce({ status: 'completed', operatorStop: null, codingEngine: null, executor: 'codex' } as never);
    await readSettled(session, 'j5');
    expect(useJobsStore.getState().extras.j5).toMatchObject({ engine: 'codex' });
  });

  it('a live read that finds it ended is the ended read', async () => {
    await loadJobTimeline(session, 'j3', { ended: false });
    await loadJobTimeline(session, 'j3', { ended: true });
    expect(api.timeline).toHaveBeenCalledTimes(1);
  });

  it('R31-AXET-FU-3: an ended read the server still calls running is not kept as final; the next ended read reads', async () => {
    api.timeline.mockResolvedValueOnce({ status: 'running', operatorStop: null, codingEngine: null });
    await readSettled(session, 'j6');
    expect(useJobsStore.getState().timelines.j6?.ended).toBe(false);
    await loadJobTimeline(session, 'j6', { ended: true });
    expect(api.timeline).toHaveBeenCalledTimes(2);
    expect(useJobsStore.getState().timelines.j6).toMatchObject({ ended: true, view: { status: 'completed' } });
    expect(useJobsStore.getState().extras.j6).toMatchObject({ settled: true, engine: 'opencode' });
    await loadJobTimeline(session, 'j6', { ended: true });
    expect(api.timeline).toHaveBeenCalledTimes(2);
  });

  it('force reads again (a decision changed it)', async () => {
    await loadJobTimeline(session, 'j4', { ended: true });
    await loadJobTimeline(session, 'j4', { ended: true, force: true });
    expect(api.timeline).toHaveBeenCalledTimes(2);
  });
});

describe('F3: a follower that may not read the timeline gets no settle read', () => {
  it('a viewer follows the stream to its end and reads nothing else', async () => {
    streams.push((h) => {
      h.onEvent(frame('v1', 'RUN_STARTED', '1'));
      h.onEvent(frame('v1', 'RUN_FINISHED', '2'));
    });
    const release = followJob(session, 'v1', { settle: false });
    await vi.waitFor(() => expect(useJobsStore.getState().streams.v1?.ended).toBe(true));
    followJob(session, 'v1', { settle: false })();
    release();
    await Promise.resolve();
    expect(api.timeline).not.toHaveBeenCalled();
  });

  it('its owner, following the same job, does read it once', async () => {
    streams.push((h) => {
      h.onEvent(frame('o1', 'RUN_STARTED', '1'));
      h.onEvent(frame('o1', 'RUN_FINISHED', '2'));
    });
    const release = followJob(session, 'o1');
    await vi.waitFor(() => expect(api.timeline).toHaveBeenCalledTimes(1));
    release();
  });
});

describe('F9: a Stop is the job’s, not the card’s', () => {
  it('a 409 is kept for the job, and a second click sends nothing', async () => {
    expect(await stopJob(session, 's1')).toBe('gone');
    expect(useJobsStore.getState().stops.s1).toBe('gone');
    expect(await stopJob(session, 's1')).toBe('gone');
    expect(api.stop).toHaveBeenCalledTimes(1);
  });

  it('a failed Stop can be tried again', async () => {
    api.stop.mockRejectedValueOnce(new Error('offline'));
    expect(await stopJob(session, 's2')).toBe('failed');
    api.stop.mockResolvedValueOnce(true);
    expect(await stopJob(session, 's2')).toBe('sent');
    expect(api.stop).toHaveBeenCalledTimes(2);
  });
});

describe('F6: a job’s status only moves forward', () => {
  const snapshot = (status: string) => ({ type: 'STATE_SNAPSHOT', snapshot: { run_id: 'r', status } });

  it('a running snapshot starts it; a queued one after that does not send it back', () => {
    let state = reduceJobFrame(emptyJobStream('r'), snapshot('queued'));
    expect(state.status).toBe('queued');
    state = reduceJobFrame(state, snapshot('running'));
    expect(state.status).toBe('running');
    state = reduceJobFrame(state, snapshot('queued'));
    expect(state.status).toBe('running');
  });
});

describe('F3: a viewer following the owner’s run live reads none of its owner routes', () => {
  it('the activity run is none for someone else’s chat, and the chat still reads as durable', async () => {
    const { activityRun } = await import('@/chat/activity');
    const { conversationHarness } = await import('@/chat/assistant-harness');
    const durableRun = { sessionId: 'c1', runId: 'r1' };
    const viewer = { convId: 'c1', threadAgentId: null, durableRun, lastDurableRun: null, messages: [], isOwner: false };
    expect(activityRun(viewer as never)).toBeNull();
    expect(conversationHarness(viewer as never)).toBe('durable');
    expect(activityRun({ ...viewer, isOwner: true } as never)).toEqual(durableRun);
  });
});
