/**
 * Loaded by .mocharc.yaml `require`: names the test file whose leftover work failed after it finished.
 *
 * When a file ends, mocha's parallel worker removes every 'uncaughtException' and 'unhandledRejection'
 * listener. Work a finished test left running (a retry timer, a background task) that throws or rejects
 * before the next file's run starts then kills the worker: "Workerpool Worker terminated Unexpectedly",
 * with no test named. This keeps a listener there between files that records the error instead; the next
 * file's root afterAll fails, naming the file that ran before it. During a file's run mocha's own
 * listeners handle these errors as before.
 */
import type { Context } from 'mocha';

const EVENTS = ['uncaughtException', 'unhandledRejection'] as const;
const strays: string[] = [];
let lastFile = '(no test has run in this worker yet)';
let inFile = false;

const record = (event: string) => (error: unknown): void => {
  if (inFile) return;
  const line = `${event} after ${lastFile} finished: ${error instanceof Error ? error.stack : String(error)}`;
  // Printed now as well: after a worker's last file, no later afterAll can report it.
  process.stderr.write(`\n${line}\n`);
  strays.push(line);
};
const recorders = Object.fromEntries(EVENTS.map((event) => [event, record(event)]));

// Typed as 'unhandledRejection': both events accept a listener whose first argument is the error.
const listen = (event: (typeof EVENTS)[number]): void => {
  process.off(event as 'unhandledRejection', recorders[event]!);
  process.on(event as 'unhandledRejection', recorders[event]!);
};

if (process.env.MOCHA_WORKER_ID !== undefined) {
  const removeAll = process.removeAllListeners.bind(process);
  process.removeAllListeners = ((event?: string | symbol) => {
    removeAll(event);
    for (const own of EVENTS) {
      if (event !== undefined && event !== own) continue;
      inFile = false;
      listen(own);
    }
    return process;
  }) as typeof process.removeAllListeners;
  EVENTS.forEach(listen);
}

export const mochaHooks = {
  beforeAll(): void {
    inFile = true;
  },
  beforeEach(this: Context): void {
    lastFile = this.currentTest?.file ?? lastFile;
  },
  afterAll(): void {
    if (strays.length === 0) return;
    throw new Error(
      `work left running by an earlier test file failed after that file finished; ` +
        `stop or await it before the test ends:\n${strays.splice(0).join('\n')}`,
    );
  },
};
