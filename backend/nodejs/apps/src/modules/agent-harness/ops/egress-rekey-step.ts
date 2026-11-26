import { spawn } from 'node:child_process';
import {
  emptyReport,
  type RotationStep,
  type StepContext,
  type StepReport,
} from '../application/secret-key-rotation';

/** How the step starts the Python side; the CLI's own process environment is passed on (SECRET_KEY and the ring). */
export interface PythonCommand {
  executable: string;
  cwd: string;
}

export type RunProcess = (
  command: PythonCommand,
  args: readonly string[],
) => Promise<{ code: number | null; stdout: string }>;

const runProcess: RunProcess = (command, args) =>
  new Promise((resolve, reject) => {
    const child = spawn(command.executable, [...args], {
      cwd: command.cwd,
      env: process.env,
      // Its stderr is the operator's to see; stdout carries the one JSON line of counts.
      stdio: ['ignore', 'pipe', 'inherit'],
    });
    let stdout = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk;
    });
    child.on('error', reject);
    child.on('close', (code) => resolve({ code, stdout }));
  });

const COUNTS = [
  'total',
  'current',
  'previous',
  'moved',
  'unreadable',
  'draining',
] as const;

/**
 * A step whose data the Python side owns and seals: `python -m <module> [--apply]`, run where the Python services
 * run, printing one JSON line of counts (`app/utils/encryption/rotation_step.py`).
 */
export class PythonRotationStep implements RotationStep {
  constructor(
    readonly name: string,
    private readonly module: string,
    private readonly label: string,
    private readonly command: PythonCommand,
    private readonly runner: RunProcess = runProcess,
  ) {}

  async run(context: StepContext): Promise<StepReport> {
    context.checkpoint();
    let outcome: { code: number | null; stdout: string };
    try {
      outcome = await this.runner(this.command, [
        '-m',
        this.module,
        ...(context.apply ? ['--apply'] : []),
      ]);
    } catch (error) {
      throw new Error(
        `${this.label} could not start ${this.command.executable} in ${this.command.cwd} (${
          error instanceof Error ? error.name : 'error'
        }): pass --python and --python-dir`,
        { cause: error },
      );
    }
    const line = outcome.stdout.trim().split('\n').at(-1) ?? '';
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line) as Record<string, unknown>;
    } catch {
      throw new Error(
        `${this.label} printed no report (exit ${String(outcome.code)})`,
      );
    }
    if (outcome.code !== 0 || typeof parsed['error'] === 'string') {
      throw new Error(
        `${this.label} failed: ${typeof parsed['error'] === 'string' ? parsed['error'] : `exit ${String(outcome.code)}`}`,
      );
    }
    const report = emptyReport(this.name);
    for (const field of COUNTS) {
      const value = parsed[field];
      if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
        throw new Error(`${this.label}'s report has no count "${field}"`);
      }
      report[field] = value;
    }
    context.progress(report);
    return report;
  }
}

/**
 * The egress proxy's durable state lives in the store the Python proxy owns
 * and is sealed by its code.
 */
export class EgressStateRekeyStep extends PythonRotationStep {
  constructor(command: PythonCommand, runner?: RunProcess) {
    super(
      'egress-state',
      'app.harness.sandbox.egress_rekey',
      'the egress state step',
      command,
      runner,
    );
  }
}

/**
 * Connector sync points seal their delta links with the platform's key; the
 * graph holds them, read through the Python graph provider on any backend
 * (WP87-FU-4b), so `retire` sees one still under the previous key.
 */
export class SyncPointRekeyStep extends PythonRotationStep {
  constructor(command: PythonCommand, runner?: RunProcess) {
    super(
      'sync-points',
      'app.connectors.core.base.sync_point.rekey',
      'the sync point step',
      command,
      runner,
    );
  }
}
