// Mocha's worker pool sends SIGTERM to any worker still running 1s after it is told to stop.
// Under c8 a worker writes its coverage file while exiting, which can take longer than that,
// and the default SIGTERM action kills it mid-write so that worker's coverage is silently lost.
// A listener replaces the default action, letting the write finish.
if (process.env.MOCHA_WORKER_ID !== undefined && process.env.NODE_V8_COVERAGE) {
  process.on('SIGTERM', () => process.exit());
}
