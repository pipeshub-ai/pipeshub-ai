/**
 * Named crash points; same semantics as `harness_core/failpoints.py`.
 *
 * `failpoint(name)` is a no-op unless armed in-process (`registry.arm`,
 * `armed`) or through a JSON file installed with `registry.installFile(path)`
 * (the path comes from the test harness; there is no env var). File format:
 *
 *   {"node.admit.after_commit": {"action": "kill9", "trigger": {"nth": 2}}}
 */
import * as fs from 'fs';

export class FailpointError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FailpointError';
  }
}

export type FailpointAction =
  | { kind: 'raise'; message?: string; error?: () => Error }
  | { kind: 'delay'; ms: number }
  | { kind: 'kill9' };

export type FailpointTrigger =
  | { kind: 'once' }
  | { kind: 'nth'; k: number }
  | { kind: 'always' };

export interface FailpointArm {
  action: FailpointAction;
  trigger?: FailpointTrigger;
}

interface ArmedPoint {
  action: FailpointAction;
  trigger: FailpointTrigger;
  hits: number;
}

const fires = (trigger: FailpointTrigger, hit: number): boolean => {
  switch (trigger.kind) {
    case 'always':
      return true;
    case 'nth':
      return hit === trigger.k;
    case 'once':
      return hit === 1;
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const parseTrigger = (raw: unknown): FailpointTrigger => {
  if (raw === undefined || raw === 'once') return { kind: 'once' };
  if (raw === 'always') return { kind: 'always' };
  if (isRecord(raw) && typeof raw.nth === 'number') return nth(raw.nth);
  throw new Error(`unknown failpoint trigger: ${JSON.stringify(raw)}`);
};

const parseAction = (spec: Record<string, unknown>): FailpointAction => {
  switch (spec.action) {
    case 'kill9':
      return { kind: 'kill9' };
    case 'delay':
      if (typeof spec.ms !== 'number')
        throw new Error('delay needs numeric ms');
      return { kind: 'delay', ms: spec.ms };
    case 'raise':
      return {
        kind: 'raise',
        message: typeof spec.message === 'string' ? spec.message : '',
      };
    default:
      throw new Error(
        `unknown failpoint action: ${JSON.stringify(spec.action)}`,
      );
  }
};

export const parseArms = (raw: unknown): Map<string, FailpointArm> => {
  if (!isRecord(raw)) throw new Error('failpoint file must hold a JSON object');
  const arms = new Map<string, FailpointArm>();
  for (const [name, spec] of Object.entries(raw)) {
    if (!isRecord(spec)) throw new Error(`failpoint ${name} must be an object`);
    arms.set(name, {
      action: parseAction(spec),
      trigger: parseTrigger(spec.trigger),
    });
  }
  return arms;
};

class FileSource {
  private mtimeMs: number | null = null;

  constructor(readonly path: string) {}

  changed(): boolean {
    const mtimeMs = fs.existsSync(this.path)
      ? fs.statSync(this.path).mtimeMs
      : null;
    if (mtimeMs === this.mtimeMs) return false;
    this.mtimeMs = mtimeMs;
    return true;
  }

  read(): Map<string, FailpointArm> {
    if (!fs.existsSync(this.path)) return new Map();
    return parseArms(JSON.parse(fs.readFileSync(this.path, 'utf-8')));
  }
}

export class FailpointRegistry {
  private points = new Map<string, ArmedPoint>();
  private file: FileSource | null = null;

  get active(): boolean {
    return this.points.size > 0 || this.file !== null;
  }

  arm(name: string, arm: FailpointArm | FailpointAction): void {
    const resolved: FailpointArm = 'action' in arm ? arm : { action: arm };
    this.points.set(name, {
      action: resolved.action,
      trigger: resolved.trigger ?? { kind: 'once' },
      hits: 0,
    });
  }

  disarm(name: string): void {
    this.points.delete(name);
  }

  reset(): void {
    this.points.clear();
    this.file = null;
  }

  hits(name: string): number {
    return this.points.get(name)?.hits ?? 0;
  }

  installFile(path: string): void {
    this.file = new FileSource(path);
    this.reloadFile();
  }

  take(name: string): FailpointAction | null {
    this.reloadFile();
    const point = this.points.get(name);
    if (!point) return null;
    point.hits += 1;
    return fires(point.trigger, point.hits) ? point.action : null;
  }

  private reloadFile(): void {
    if (!this.file?.changed()) return;
    this.points = new Map();
    for (const [name, arm] of this.file.read()) this.arm(name, arm);
  }
}

export const registry = new FailpointRegistry();

const raise = (
  action: Extract<FailpointAction, { kind: 'raise' }>,
  name: string,
): never => {
  if (action.error) throw action.error();
  throw new FailpointError(action.message || `failpoint ${name}`);
};

const kill9 = (): never => {
  process.kill(process.pid, 'SIGKILL');
  throw new Error('unreachable: SIGKILL did not terminate the process');
};

const blockFor = (ms: number): void => {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
};

/** Sync call site; `delay` blocks the thread (use `failpointAsync` on the event loop). */
export const failpoint = (name: string): void => {
  if (!registry.active) return;
  const action = registry.take(name);
  if (!action) return;
  if (action.kind === 'kill9') kill9();
  else if (action.kind === 'delay') blockFor(action.ms);
  else raise(action, name);
};

export const failpointAsync = async (name: string): Promise<void> => {
  if (!registry.active) return;
  const action = registry.take(name);
  if (!action) return;
  if (action.kind === 'kill9') kill9();
  else if (action.kind === 'delay')
    await new Promise<void>((r) => setTimeout(r, action.ms));
  else raise(action, name);
};

export const always = (): FailpointTrigger => ({ kind: 'always' });
export function nth(k: number): FailpointTrigger {
  if (!Number.isInteger(k) || k < 1) throw new Error('nth trigger is 1-based');
  return { kind: 'nth', k };
}

/** Arm `arms` for the duration of `fn` (sync or async), then disarm exactly those. */
export async function armed<T>(
  arms: Record<string, FailpointArm | FailpointAction>,
  fn: () => T | Promise<T>,
  target: FailpointRegistry = registry,
): Promise<T> {
  for (const [name, arm] of Object.entries(arms)) target.arm(name, arm);
  try {
    return await fn();
  } finally {
    for (const name of Object.keys(arms)) target.disarm(name);
  }
}

const triggerJson = (trigger: FailpointTrigger | undefined): unknown => {
  if (!trigger || trigger.kind === 'once') return 'once';
  if (trigger.kind === 'always') return 'always';
  return { nth: trigger.k };
};

const actionJson = (action: FailpointAction): Record<string, unknown> => {
  if (action.kind === 'raise') {
    if (action.error)
      throw new Error('a failpoint file can only raise FailpointError');
    return { action: 'raise', message: action.message ?? '' };
  }
  return action.kind === 'delay'
    ? { action: 'delay', ms: action.ms }
    : { action: 'kill9' };
};

/** Write a failpoint file for another process (atomic rename). */
export const writeArms = (
  path: string,
  arms: Record<string, FailpointArm | FailpointAction>,
): void => {
  const doc: Record<string, unknown> = {};
  for (const [name, arm] of Object.entries(arms)) {
    const resolved: FailpointArm = 'action' in arm ? arm : { action: arm };
    doc[name] = {
      ...actionJson(resolved.action),
      trigger: triggerJson(resolved.trigger),
    };
  }
  const tmp = `${path}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(doc));
  fs.renameSync(tmp, path);
};
