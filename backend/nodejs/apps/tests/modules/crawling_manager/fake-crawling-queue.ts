import { createHash } from 'crypto'
import { getNextMillis, JobsOptions, JobType, RepeatOptions } from 'bullmq'
import { CrawlingSchedulerService } from '../../../src/modules/crawling_manager/services/crawling_service'
import { CrawlingJobData } from '../../../src/modules/crawling_manager/schema/interface'

type JobState = 'waiting' | 'delayed' | 'prioritized' | 'active' | 'completed' | 'failed'
const PENDING: JobState[] = ['waiting', 'delayed', 'prioritized']

interface StoredRepeatable {
  key: string
  name: string
  id: string | null
  endDate: number | null
  tz: string | null
  pattern: string | null
  every: string | null
  next: number
}

/**
 * The Redis state behind a BullMQ queue. Several FakeCrawlingQueue objects can
 * share one store, which is how a service restart is modelled: a new scheduler
 * instance, the same queue contents.
 */
export class FakeQueueStore {
  readonly jobs = new Map<string, FakeJob>()
  readonly repeatables = new Map<string, StoredRepeatable>()
  private counter = 0
  /** When set, every queue call rejects, as it would with Redis unreachable. */
  failWith: Error | null = null

  nextId(): string {
    this.counter += 1
    return String(this.counter)
  }

  pending(): FakeJob[] {
    return [...this.jobs.values()].filter((j) => PENDING.includes(j.state))
  }
}

export class FakeJob {
  state: JobState
  progress: number | object = 0
  attemptsMade = 0
  processedOn: number | undefined
  finishedOn: number | undefined
  failedReason: string | undefined
  readonly timestamp: number

  constructor(
    private readonly store: FakeQueueStore,
    readonly id: string,
    readonly name: string,
    readonly data: CrawlingJobData,
    readonly opts: JobsOptions & { repeatJobKey?: string },
    readonly delay: number,
    now: number,
  ) {
    this.timestamp = now
    this.state = delay > 0 ? 'delayed' : 'waiting'
  }

  get runAt(): number {
    return this.timestamp + this.delay
  }

  async getState(): Promise<JobState | 'unknown'> {
    return this.store.jobs.get(this.id) === this ? this.state : 'unknown'
  }

  async updateProgress(progress: number | object): Promise<void> {
    this.progress = progress
  }

  async remove(): Promise<void> {
    if (this.state === 'active') {
      throw new Error(`Job ${this.id} could not be removed because it is locked by another worker`)
    }
    this.store.jobs.delete(this.id)
  }
}

// BullMQ 5's legacy repeat key: `name:jobId:endDate:tz:pattern-or-every`, hashed.
const repeatConcat = (name: string, repeat: RepeatOptions & { jobId?: string }): string => {
  const endDate = repeat.endDate ? new Date(repeat.endDate).getTime() : ''
  const suffix = repeat.pattern ? repeat.pattern : String(repeat.every)
  return `${name}:${repeat.jobId ?? ''}:${endDate}:${repeat.tz ?? ''}:${suffix}`
}
const md5 = (s: string): string => createHash('md5').update(s).digest('hex')

/**
 * Stands in for the BullMQ Queue the scheduler writes to. It keeps the
 * behaviour the scheduler depends on: repeatable jobs keyed by name and repeat
 * options, a delayed instance for each next run, custom job ids that are never
 * added twice, and BullMQ's own cron parsing, so a pattern BullMQ would refuse
 * is refused here too.
 */
export class FakeCrawlingQueue {
  constructor(private readonly store: FakeQueueStore) {}

  private guard(): void {
    if (this.store.failWith) throw this.store.failWith
  }

  async add(name: string, data: CrawlingJobData, opts: JobsOptions = {}): Promise<FakeJob | undefined> {
    this.guard()
    const now = Date.now()
    if (opts.repeat) {
      const repeat = { ...opts.repeat }
      const nextMillis = getNextMillis(now, repeat)
      if (!nextMillis) return undefined
      const key = md5(repeatConcat(name, { ...repeat, jobId: opts.jobId }))
      this.store.repeatables.set(key, {
        key,
        name,
        id: opts.jobId ?? null,
        endDate: null,
        tz: repeat.tz ?? null,
        pattern: repeat.pattern ?? null,
        every: repeat.every ? String(repeat.every) : null,
        next: nextMillis,
      })
      return this.createRepeatInstance(key, name, data, opts, nextMillis, now)
    }
    const id = opts.jobId ?? this.store.nextId()
    const existing = this.store.jobs.get(id)
    if (existing) return existing
    const job = new FakeJob(this.store, id, name, data, opts, opts.delay ?? 0, now)
    this.store.jobs.set(id, job)
    return job
  }

  private createRepeatInstance(
    key: string,
    name: string,
    data: CrawlingJobData,
    opts: JobsOptions,
    nextMillis: number,
    now: number,
  ): FakeJob {
    const id = `repeat:${key}:${nextMillis}`
    const existing = this.store.jobs.get(id)
    if (existing) return existing
    const job = new FakeJob(this.store, id, name, data, { ...opts, repeatJobKey: key }, Math.max(0, nextMillis - now), now)
    this.store.jobs.set(id, job)
    return job
  }

  async getJobs(types: JobType[] = []): Promise<FakeJob[]> {
    this.guard()
    return [...this.store.jobs.values()].filter((j) => types.includes(j.state))
  }

  async getRepeatableJobs(): Promise<StoredRepeatable[]> {
    this.guard()
    return [...this.store.repeatables.values()].map((r) => ({ ...r }))
  }

  async removeRepeatable(name: string, repeat: RepeatOptions, jobId?: string): Promise<boolean> {
    this.guard()
    const key = md5(repeatConcat(name, { ...repeat, jobId }))
    const existed = this.store.repeatables.delete(key)
    for (const job of [...this.store.jobs.values()]) {
      if (job.opts.repeatJobKey === key && job.state === 'delayed') this.store.jobs.delete(job.id)
    }
    return existed
  }

  private byState(state: JobState): Promise<FakeJob[]> {
    return this.getJobs([state])
  }
  getWaiting(): Promise<FakeJob[]> {
    return this.byState('waiting')
  }
  getActive(): Promise<FakeJob[]> {
    return this.byState('active')
  }
  getCompleted(): Promise<FakeJob[]> {
    return this.byState('completed')
  }
  getFailed(): Promise<FakeJob[]> {
    return this.byState('failed')
  }
  getDelayed(): Promise<FakeJob[]> {
    return this.byState('delayed')
  }

  async close(): Promise<void> {}

  /**
   * Moves the delayed jobs due by `at` on, as BullMQ does before a worker
   * picks them up: one with a priority goes to `prioritized`, not `waiting`.
   */
  promoteDue(at: number): void {
    for (const job of this.store.jobs.values()) {
      if (job.state === 'delayed' && job.runAt <= at) {
        job.state = job.opts.priority ? 'prioritized' : 'waiting'
      }
    }
  }

  /**
   * Runs every job due by `at` through `processor`, the way a BullMQ worker
   * would: a repeatable queues its next run when it starts, a failure is
   * retried until `attempts` is used up and then left in the failed set.
   */
  async runDue(processor: (job: FakeJob) => Promise<void>, at: number): Promise<FakeJob[]> {
    const due = this.store.pending().filter((j) => j.runAt <= at)
    for (const job of due) {
      job.state = 'active'
      const key = job.opts.repeatJobKey
      const repeatable = key ? this.store.repeatables.get(key) : undefined
      if (key && repeatable && job.opts.repeat) {
        const next = getNextMillis(job.runAt, job.opts.repeat)
        if (next) {
          repeatable.next = next
          this.createRepeatInstance(key, job.name, job.data, job.opts, next, job.runAt)
        }
      }
      const attempts = Math.max(1, job.opts.attempts ?? 1)
      while (job.state === 'active') {
        job.processedOn = at
        job.attemptsMade += 1
        try {
          await processor(job)
          job.state = 'completed'
          job.finishedOn = at
        } catch (error) {
          if (job.attemptsMade >= attempts) {
            job.state = 'failed'
            job.failedReason = error instanceof Error ? error.message : String(error)
            job.finishedOn = at
          }
        }
      }
    }
    return due
  }
}

export const REDIS_CONFIG = { host: 'redis.invalid', port: 6379, db: 0 }

/** The real scheduler service, with its BullMQ queue swapped for the fake over `store`. */
export const schedulerOver = (store: FakeQueueStore): { service: CrawlingSchedulerService; queue: FakeCrawlingQueue } => {
  const service = new CrawlingSchedulerService(REDIS_CONFIG)
  const queue = new FakeCrawlingQueue(store)
  Reflect.set(service, 'queue', queue)
  return { service, queue }
}
