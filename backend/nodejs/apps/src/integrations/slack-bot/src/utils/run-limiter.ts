export type RunResult<T> = { status: 'ran'; value: T } | { status: 'busy' };

export class RunLimiter {
  private active = 0;
  private readonly queue: Array<() => void> = [];

  constructor(
    private readonly maxRunning: number,
    private readonly maxWaiting: number,
  ) {}

  get running(): number {
    return this.active;
  }

  get waiting(): number {
    return this.queue.length;
  }

  async run<T>(task: () => Promise<T>): Promise<RunResult<T>> {
    if (this.active >= this.maxRunning) {
      if (this.queue.length >= this.maxWaiting) return { status: 'busy' };
      // The releasing task hands its slot over, so `active` stays unchanged.
      await new Promise<void>((resolve) => this.queue.push(resolve));
    } else {
      this.active++;
    }
    try {
      return { status: 'ran', value: await task() };
    } finally {
      const next = this.queue.shift();
      if (next) next();
      else this.active--;
    }
  }
}

const readInt = (raw: string | undefined, def: number, min: number, max: number): number => {
  const n = Number.parseInt(raw ?? '', 10);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : def;
};

export function runLimiterFromEnv(env: NodeJS.ProcessEnv = process.env): RunLimiter {
  return new RunLimiter(
    readInt(env.SLACK_BOT_MAX_CONCURRENT_RUNS, 50, 1, 500),
    readInt(env.SLACK_BOT_MAX_WAITING_RUNS, 500, 0, 5000),
  );
}
