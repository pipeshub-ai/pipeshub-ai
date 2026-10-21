import { ServiceUnavailableError } from '../../../libs/errors/http.errors';

/** Why a run ends before its stream does: this process is stopping, or someone stopped that run. */
export type RunStopReason = 'shutdown' | 'stop';

type RunStop = (reason: RunStopReason) => Promise<void>;

/**
 * The answers this process is streaming, so a shutdown can let them finish and then stop the rest,
 * each saving its partial answer while the database is still open. A run with an id can be stopped alone.
 */
const stops = new Set<RunStop>();
const stopsByRunId = new Map<string, RunStop>();
let shuttingDown = false;
let drained: (() => void) | undefined;

/** True once a shutdown began: no new run may start here. */
export const isShuttingDown = (): boolean => shuttingDown;

/** Throws the 503 that asks the client to send again, which reaches a server that is not stopping. */
export const refuseRunWhileShuttingDown = (): void => {
  if (shuttingDown) {
    throw new ServiceUnavailableError(
      'PipesHub is restarting. Send your message again in a moment.',
      { retryAfter: '1' },
    );
  }
};

/** Registers a running answer's stop, by its run id when it has one; call the returned function once the run has ended. */
export const trackRun = (stop: RunStop, runId?: string): (() => void) => {
  stops.add(stop);
  if (runId !== undefined) stopsByRunId.set(runId, stop);
  return () => {
    stops.delete(stop);
    if (runId !== undefined) stopsByRunId.delete(runId);
    if (stops.size === 0) drained?.();
  };
};

/** Stops the run with this id and waits for its save; false when this process is not streaming it. */
export const stopRun = async (runId: string): Promise<boolean> => {
  const stop = stopsByRunId.get(runId);
  if (!stop) return false;
  await stop('stop');
  return true;
};

const atMost = async (work: Promise<unknown>, ms: number): Promise<void> => {
  let timer: NodeJS.Timeout | undefined;
  await Promise.race([
    work,
    new Promise((resolve) => {
      timer = setTimeout(resolve, ms);
    }),
  ]);
  clearTimeout(timer);
};

/** Refuses new runs from now on, and waits up to `ms` for the running ones to end. */
export const drainRuns = async (ms: number): Promise<void> => {
  shuttingDown = true;
  if (stops.size === 0) return;
  await atMost(
    new Promise<void>((resolve) => {
      drained = resolve;
    }),
    ms,
  );
};

/** Stops every run still going and waits at most `ms` for their saves; returns how many there were. */
export const stopAllRuns = async (ms: number): Promise<number> => {
  const running = [...stops];
  if (running.length > 0) {
    await atMost(
      Promise.allSettled(running.map(async (stop) => stop('shutdown'))),
      ms,
    );
  }
  return running.length;
};

/** Test hook: forgets every run and the shutdown. */
export const resetActiveRunsForTests = (): void => {
  stops.clear();
  stopsByRunId.clear();
  shuttingDown = false;
  drained = undefined;
};
