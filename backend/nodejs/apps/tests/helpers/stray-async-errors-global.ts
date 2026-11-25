/**
 * Loaded by .mocharc.yaml `require`: names the test file whose leftover work failed after it finished.
 *
 * When a file ends, mocha's parallel worker removes every 'uncaughtException' and 'unhandledRejection'
 * listener. Work a finished test left running (a retry timer, a background task) that throws or rejects
 * before the next file's run starts then kills the worker: "Workerpool Worker terminated Unexpectedly",
 * with no test named. This keeps a listener there between files that records the error instead; the next
 * file's root afterAll fails, naming the file that ran before it. An error no later afterAll reports (the
 * worker ran its last file, or its later files selected no test) fails the run at its end, from the main
 * process's global teardown, which reads what each worker left in the report folder. During a file's run
 * mocha's own listeners handle these errors as before.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Context } from 'mocha';

const EVENTS = ['uncaughtException', 'unhandledRejection'] as const;
const strays: string[] = [];
let lastFile = '(no test has run in this worker yet)';
let inFile = false;

// Set by the main process's global setup; each worker writes the errors it has not reported yet to its own file.
const DIR_VARIABLE = 'STRAY_ASYNC_ERRORS_DIR';
const save = (): void => {
  const dir = process.env[DIR_VARIABLE];
  if (dir) fs.writeFileSync(path.join(dir, `${process.pid}.log`), strays.join('\n'));
};

const record = (event: string) => (error: unknown): void => {
  if (inFile) return;
  const line = `${event} after ${lastFile} finished: ${error instanceof Error ? error.stack : String(error)}`;
  process.stderr.write(`\n${line}\n`);
  strays.push(line);
  save();
};
const recorders = Object.fromEntries(EVENTS.map((event) => [event, record(event)]));

// Typed as 'unhandledRejection': both events accept a listener whose first argument is the error.
const listen = (event: (typeof EVENTS)[number]): void => {
  process.off(event as 'unhandledRejection', recorders[event]!);
  process.on(event as 'unhandledRejection', recorders[event]!);
};

if (process.env.MOCHA_WORKER_ID !== undefined) {
  const removeAll = process.removeAllListeners.bind(process);
  process.removeAllListeners = ((...args: [(string | symbol)?]) => {
    // Forwarded as given: Node removes every listener only when called with no argument at all.
    removeAll(...args);
    const [event] = args;
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
    const lines = strays.splice(0);
    save();
    throw new Error(
      `work left running by an earlier test file failed after that file finished; ` +
        `stop or await it before the test ends:\n${lines.join('\n')}`,
    );
  },
};

export const mochaGlobalSetup = (): void => {
  process.env[DIR_VARIABLE] = fs.mkdtempSync(path.join(os.tmpdir(), 'stray-async-errors-'));
};

export const mochaGlobalTeardown = (): void => {
  const dir = process.env[DIR_VARIABLE];
  if (!dir) return;
  const lines = fs
    .readdirSync(dir)
    .map((name) => fs.readFileSync(path.join(dir, name), 'utf8'))
    .filter((text) => text !== '');
  fs.rmSync(dir, { recursive: true, force: true });
  delete process.env[DIR_VARIABLE];
  if (lines.length === 0) return;
  throw new Error(
    `work left running by a test file failed after that file finished, and no later file in its worker ` +
      `reported it; stop or await it before the test ends:\n${lines.join('\n')}`,
  );
};
