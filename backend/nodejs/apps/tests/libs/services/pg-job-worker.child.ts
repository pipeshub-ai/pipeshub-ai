// A second Node process for pg-job-queue.itest.ts: runs a worker on the queue named in SCHED_TEST_QUEUE and prints one line per run it starts.
import 'reflect-metadata'
import { PgJobWorker } from '../../../src/libs/services/pg-job-queue.service'

const queue = process.env.SCHED_TEST_QUEUE ?? ''
const w = new PgJobWorker<Record<string, unknown>>(queue, async (j) => {
  process.stdout.write(`RUN ${j.id}\n`)
  await new Promise((r) => setTimeout(r, 20))
}, { concurrency: 3 })
process.stdout.write('READY\n')
process.on('SIGTERM', () => { void w.close().then(() => process.exit(0)) })
setInterval(() => undefined, 1000)
