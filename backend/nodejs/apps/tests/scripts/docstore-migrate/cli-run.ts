/** Runs a command of the command line in this process, on the world's servers, and returns its exit code and the JSON it printed. */
import { runCli } from '../../../src/scripts/docstore-migrate/cli';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import type { World } from './world';

export async function runCommand(w: World, args: string[], extra: NodeJS.ProcessEnv = {}): Promise<{ code: number; out: any }> {
  let text = '';
  const out = process.stdout.write.bind(process.stdout);
  const err = process.stderr.write.bind(process.stderr);
  (process.stdout as any).write = (s: string): boolean => ((text += s), true);
  (process.stderr as any).write = (): boolean => true;
  try {
    // a command opens its own runtime, and a process holds one: the suite's is closed for the call and opened again after
    await w.rt.close();
    const code = await runCli(args, { ...w.env, ...extra });
    // most commands print one JSON document; help prints text
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = { text };
    }
    return { code, out: parsed as any };
  } finally {
    (process.stdout as any).write = out;
    (process.stderr as any).write = err;
    w.rt = await MigrationRuntime.open(loadConfig(w.env));
  }
}
