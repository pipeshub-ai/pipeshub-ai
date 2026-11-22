import { PgClient, PgPool } from '../../../src/libs/db/uow'

export type Result = { rows?: any[]; rowCount?: number; command?: string }
export type Responder = (text: string, values: unknown[] | undefined, n: number) => Result | Promise<Result>

export interface Call { text: string; values?: unknown[]; name?: string }

/** Scripted stand-in for pg.Pool. The default responder answers the prologue with role 'none'. */
export class FakePool implements PgPool {
  calls: Call[] = []
  connects = 0
  /** what `getTransactionStatus()` answers */
  status: () => string = () => 'T'
  released: Array<boolean | Error | undefined> = []
  constructor(public responder: Responder = () => ({ rows: [] }), public connectError?: Error) {}

  async connect(): Promise<PgClient> {
    this.connects++
    if (this.connectError) throw this.connectError
    return {
      query: async (q) => {
        this.calls.push(q)
        if (/^WITH pre AS/.test(q.text)) {
          const r = await this.responder(q.text, q.values, this.calls.length)
          return { rows: r.rows?.length ? r.rows : [{ r: 'none', fence: null, ts: 'T0' }], rowCount: 1 }
        }
        if (/pipeshub:kernel\.commit_probe/.test(q.text)) {
          const r = await this.responder(q.text, q.values, this.calls.length)
          return { rows: r.rows?.length ? r.rows : [{ ts: 'T0' }], rowCount: 1 }
        }
        if (/^(BEGIN|COMMIT|ROLLBACK)/.test(q.text)) {
          const r = await this.responder(q.text, q.values, this.calls.length)
          return { rows: r.rows ?? [], rowCount: null, command: r.command }
        }
        const r = await this.responder(q.text, q.values, this.calls.length)
        return { rows: r.rows ?? [], rowCount: r.rowCount ?? (r.rows?.length ?? 0) }
      },
      getTransactionStatus: () => this.status(),
      release: (d) => { this.released.push(d) },
    }
  }

  get userCalls(): Call[] { return this.calls.filter((c) => !/^(BEGIN|COMMIT|ROLLBACK$|WITH pre AS|\/\* pipeshub:kernel\.commit_probe)/.test(c.text)) }
  get prologue(): Call { return this.calls.find((c) => /^WITH pre AS/.test(c.text)) as Call }
}

export function pgError(code: string, message = code): Error {
  return Object.assign(new Error(message), { code })
}
