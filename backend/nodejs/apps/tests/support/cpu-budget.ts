/**
 * CPU time of work on the calling (event loop) thread, for guards against quadratic or backtracking code.
 * Wall time on a shared test host also counts waiting for a core and other workers' load; this does not.
 * A Node without process.threadCpuUsage (added in 23.9) falls back to the whole process's CPU.
 */
const threadCpuUs = (): number => {
  const usage = typeof process.threadCpuUsage === 'function' ? process.threadCpuUsage() : process.cpuUsage();
  return usage.user + usage.system;
};

/** Milliseconds of this thread's CPU that `work` used; the fastest of `runs`, which leaves out a one-off GC or JIT pause. */
export function threadCpuMs(work: () => unknown, runs = 1): number {
  let fastest = Infinity;
  for (let i = 0; i < runs; i++) {
    const started = threadCpuUs();
    work();
    fastest = Math.min(fastest, (threadCpuUs() - started) / 1000);
  }
  return fastest;
}

/** As threadCpuMs, for async work whose CPU runs on this thread (its awaits on stubs, not on real I/O, add nothing). */
export async function threadCpuMsAsync(work: () => Promise<unknown>): Promise<number> {
  const started = threadCpuUs();
  await work();
  return (threadCpuUs() - started) / 1000;
}
