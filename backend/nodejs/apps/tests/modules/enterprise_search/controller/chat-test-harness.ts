import { EventEmitter } from 'events'
import sinon from 'sinon'
import mongoose, { Types } from 'mongoose'
import { MongoExpiredSessionError } from 'mongodb'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'

/**
 * Test doubles for the chat controller's outermost I/O: MongoDB (an in-memory
 * store that evaluates the controller's real query filters), the Python AI
 * service (a `fetch` stand-in that streams SSE frames on demand) and the
 * browser's SSE connection. Everything between those edges runs for real.
 */

type Doc = Record<string, unknown>
type Filter = Record<string, unknown>
type SessionDoc = InstanceType<typeof ChatSession>
type MessageDoc = InstanceType<typeof ChatSessionMessage>

const isObjectId = (value: unknown): value is Types.ObjectId =>
  value instanceof Types.ObjectId ||
  (value !== null && typeof value === 'object' && (value as { _bsontype?: unknown })._bsontype === 'ObjectId')

const normalize = (value: unknown): unknown => (isObjectId(value) ? value.toHexString() : value)

const isPlainObject = (value: unknown): value is Doc =>
  value !== null &&
  typeof value === 'object' &&
  !Array.isArray(value) &&
  !isObjectId(value) &&
  !(value instanceof Date) &&
  !(value instanceof RegExp)

const valuesAt = (value: unknown, path: string[]): unknown[] => {
  if (path.length === 0) return [value]
  if (Array.isArray(value)) return value.flatMap((item) => valuesAt(item, path))
  if (!isPlainObject(value)) return [undefined]
  const [head, ...rest] = path
  return valuesAt(value[head as string], rest)
}

const equals = (candidate: unknown, expected: unknown): boolean => {
  if (Array.isArray(candidate)) return candidate.some((item) => equals(item, expected))
  const a = normalize(candidate)
  const b = normalize(expected)
  if (b === null) return a === null || a === undefined
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime()
  return a === b
}

const compare = (candidate: unknown, bound: unknown, test: (a: number, b: number) => boolean): boolean => {
  const toNumber = (v: unknown): number => (v instanceof Date ? v.getTime() : Number(v))
  return candidate !== undefined && candidate !== null && test(toNumber(candidate), toNumber(bound))
}

const matchesCondition = (candidates: unknown[], condition: unknown): boolean => {
  const isOperator = isPlainObject(condition) && Object.keys(condition).some((key) => key.startsWith('$'))
  if (!isOperator) return candidates.some((candidate) => equals(candidate, condition))
  return Object.entries(condition).every(([operator, argument]) => {
    switch (operator) {
      case '$eq':
        return candidates.some((c) => equals(c, argument))
      case '$ne':
        return !candidates.some((c) => equals(c, argument))
      case '$in':
        return (argument as unknown[]).some((a) => candidates.some((c) => equals(c, a)))
      case '$nin':
        return !(argument as unknown[]).some((a) => candidates.some((c) => equals(c, a)))
      case '$exists':
        return candidates.some((c) => c !== undefined) === Boolean(argument)
      case '$gte':
        return candidates.some((c) => compare(c, argument, (a, b) => a >= b))
      case '$lte':
        return candidates.some((c) => compare(c, argument, (a, b) => a <= b))
      case '$regex': {
        const flags = typeof condition.$options === 'string' ? condition.$options : ''
        const pattern = new RegExp(String(argument), flags)
        return candidates.some((c) => typeof c === 'string' && pattern.test(c))
      }
      case '$options':
        return true
      default:
        throw new Error(`The in-memory store does not understand ${operator}; extend matchesFilter before relying on it`)
    }
  })
}

/** Evaluates a MongoDB query filter against one plain document, the way the server would. */
export const matchesFilter = (doc: Doc, filter: Filter): boolean =>
  Object.entries(filter).every(([key, condition]) => {
    if (key === '$or') return (condition as Filter[]).some((f) => matchesFilter(doc, f))
    if (key === '$and') return (condition as Filter[]).every((f) => matchesFilter(doc, f))
    if (key.startsWith('$')) throw new Error(`The in-memory store does not understand ${key}`)
    return matchesCondition(valuesAt(doc, key.split('.')), condition)
  })

interface QueryState {
  sort?: Record<string, 1 | -1>
  skip?: number
  limit?: number
  lean: boolean
  session?: unknown
}

/** Chainable, awaitable stand-in for a Mongoose query. */
class FakeQuery<T> implements PromiseLike<T> {
  private readonly state: QueryState = { lean: false }

  constructor(private readonly run: (state: QueryState) => T) {}

  sort(sort: Record<string, 1 | -1>): this {
    this.state.sort = sort
    return this
  }
  skip(skip: number): this {
    this.state.skip = skip
    return this
  }
  limit(limit: number): this {
    this.state.limit = limit
    return this
  }
  lean(): this {
    this.state.lean = true
    return this
  }
  select(): this {
    return this
  }
  populate(): this {
    return this
  }
  session(session: unknown): this {
    assertSessionUsable({ session })
    this.state.session = session
    return this
  }
  exec(): Promise<T> {
    return Promise.resolve().then(() => this.run(this.state))
  }
  then<R1 = T, R2 = never>(
    onFulfilled?: ((value: T) => R1 | PromiseLike<R1>) | null,
    onRejected?: ((reason: unknown) => R2 | PromiseLike<R2>) | null,
  ): Promise<R1 | R2> {
    return this.exec().then(onFulfilled, onRejected)
  }
}

interface StoredDoc {
  toObject(): unknown
  $session(session?: unknown): unknown
}

/** Mongoose ties a query's session to every document it returns (`lib/query.js`), so a later bare `save()` reuses it. */
const bindSession = <D extends StoredDoc>(doc: D, state: QueryState): D => {
  if (state.session != null) doc.$session(state.session)
  return doc
}

const shape = (docs: StoredDoc[], state: QueryState): unknown[] => {
  let rows = [...docs]
  const sortEntries = Object.entries(state.sort ?? {})
  if (sortEntries.length > 0) {
    rows.sort((x, y) => {
      for (const [field, direction] of sortEntries) {
        const a = normalize((x as unknown as Doc)[field]) as number | string
        const b = normalize((y as unknown as Doc)[field]) as number | string
        if (a < b) return -direction
        if (a > b) return direction
      }
      return 0
    })
  }
  if (state.skip) rows = rows.slice(state.skip)
  if (state.limit) rows = rows.slice(0, state.limit)
  return state.lean ? rows.map((row) => row.toObject()) : rows.map((row) => bindSession(row, state))
}

type SessionOption = { session?: unknown } | null | undefined

const plain = (doc: { toObject(): unknown }): Doc => doc.toObject() as Doc

/** Mirrors the MongoDB driver and Mongoose, which refuse any operation on an ended session. */
const assertSessionUsable = (options: SessionOption): void => {
  const session = options?.session as { hasEnded?: boolean } | undefined
  if (session?.hasEnded === true) {
    throw new MongoExpiredSessionError('Use of expired sessions is not permitted')
  }
}

const applyUpdate = (doc: SessionDoc | MessageDoc, update: Doc): void => {
  const operators = Object.keys(update).some((key) => key.startsWith('$'))
  const set = (operators ? update.$set : update) as Doc | undefined
  for (const [path, value] of Object.entries(set ?? {})) doc.set(path, value)
  for (const path of Object.keys((update.$unset as Doc | undefined) ?? {})) doc.set(path, undefined)
  for (const [path, value] of Object.entries((update.$inc as Doc | undefined) ?? {})) {
    doc.set(path, Number(doc.get(path) ?? 0) + Number(value))
  }
  for (const [path, value] of Object.entries((update.$push as Doc | undefined) ?? {})) {
    const current = (doc.get(path) as unknown[] | undefined) ?? []
    doc.set(path, [...current, value])
  }
}

/** Every chat session and message the controller reads or writes, held in memory. */
export class InMemoryChatStore {
  readonly sessions: SessionDoc[] = []
  readonly messages: MessageDoc[] = []
  /** One entry per write the controller performed, so a test can prove nothing was written. */
  readonly writes: string[] = []

  addSession(fields: Doc): SessionDoc {
    const doc = new ChatSession({ lastActivityAt: Date.now(), status: 'Complete', ...fields })
    this.sessions.push(doc)
    return doc
  }

  addMessage(session: SessionDoc, fields: Doc): MessageDoc {
    const seq = ((session.get('nextSeq') as number | undefined) ?? 0) + 1
    session.set('nextSeq', seq)
    const doc = new ChatSessionMessage({ sessionId: session._id, orgId: session.orgId, seq, ...fields })
    this.messages.push(doc)
    return doc
  }

  session(id: unknown): SessionDoc | undefined {
    return this.sessions.find((s) => String(s._id) === String(id))
  }

  messagesOf(sessionId: unknown): Doc[] {
    return this.messages
      .filter((m) => String(m.sessionId) === String(sessionId))
      .sort((a, b) => a.seq - b.seq)
      .map(plain)
  }

  private findSessions(filter: Filter): SessionDoc[] {
    return this.sessions.filter((s) => matchesFilter(plain(s), filter))
  }

  private findMessages(filter: Filter): MessageDoc[] {
    return this.messages.filter((m) => matchesFilter(plain(m), filter))
  }

  install(): void {
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const store = this
    const one = (doc: StoredDoc | undefined, state: QueryState): unknown =>
      doc === undefined ? null : state.lean ? doc.toObject() : bindSession(doc, state)
    const withSession = <T>(query: FakeQuery<T>, options: SessionOption): FakeQuery<T> =>
      options?.session != null ? query.session(options.session) : query

    // Real Mongoose `save()` runs, so its session rules apply: `save({ session })` ties the
    // session to the document and a later bare `save()` reuses it. Only the collection
    // write, the driver call, is faked, and it refuses an ended session as the driver does.
    const acknowledge = (options: SessionOption): Promise<Record<string, unknown>> => {
      assertSessionUsable(options)
      return Promise.resolve({ acknowledged: true, matchedCount: 1, modifiedCount: 1 })
    }
    for (const collection of [ChatSession.collection, Citation.collection]) {
      sinon.stub(collection, 'insertOne').callsFake(((_doc: unknown, options?: SessionOption) => acknowledge(options)) as never)
      sinon.stub(collection, 'updateOne').callsFake(((_filter: unknown, _update: unknown, options?: SessionOption) =>
        acknowledge(options)) as never)
    }
    const realSessionSave = ChatSession.prototype.save
    sinon.stub(ChatSession.prototype, 'save').callsFake(async function (this: SessionDoc, options?: SessionOption) {
      store.writes.push('chatSession.save')
      const saved = await realSessionSave.call(this, options as never)
      if (!store.sessions.includes(this)) store.sessions.push(this)
      return saved
    } as never)
    sinon.stub(ChatSession, 'findOne').callsFake(((filter: Filter, _projection?: unknown, options?: SessionOption) => {
      assertSessionUsable(options)
      return withSession(new FakeQuery((state) => one(store.findSessions(filter)[0], state)), options)
    }) as never)
    sinon.stub(ChatSession, 'findById').callsFake(((id: unknown) =>
      new FakeQuery((state) => one(store.session(id), state))) as never)
    sinon.stub(ChatSession, 'find').callsFake(((filter: Filter) =>
      new FakeQuery((state) => shape(store.findSessions(filter), state))) as never)
    sinon.stub(ChatSession, 'countDocuments').callsFake(((filter: Filter) =>
      new FakeQuery(() => store.findSessions(filter).length)) as never)
    sinon.stub(ChatSession, 'findOneAndUpdate').callsFake(((filter: Filter, update: Doc, options?: SessionOption) => {
      assertSessionUsable(options)
      const doc = store.findSessions(filter)[0]
      if (doc) {
        applyUpdate(doc, update)
        store.writes.push(update.$inc ? 'chatSession.allocateSeq' : 'chatSession.update')
      }
      return new FakeQuery((state) => one(doc, state))
    }) as never)

    sinon.stub(ChatSessionMessage, 'insertMany').callsFake(((docs: Doc[], options?: SessionOption) => {
      assertSessionUsable(options)
      const inserted = docs.map((fields) => new ChatSessionMessage(fields))
      store.messages.push(...inserted)
      store.writes.push('message.insert')
      return Promise.resolve(inserted)
    }) as never)
    sinon.stub(ChatSessionMessage, 'find').callsFake(((filter: Filter, _projection?: unknown, options?: SessionOption) => {
      assertSessionUsable(options)
      return withSession(new FakeQuery((state) => shape(store.findMessages(filter), state)), options)
    }) as never)
    sinon.stub(ChatSessionMessage, 'countDocuments').callsFake(((filter: Filter) =>
      new FakeQuery(() => store.findMessages(filter).length)) as never)
    sinon.stub(ChatSessionMessage, 'findOne').callsFake(((filter: Filter, _projection?: unknown, options?: SessionOption) => {
      assertSessionUsable(options)
      return withSession(new FakeQuery((state) => one(store.findMessages(filter)[0], state)), options)
    }) as never)
    sinon.stub(ChatSessionMessage, 'findById').callsFake(((id: unknown, _projection?: unknown, options?: SessionOption) => {
      assertSessionUsable(options)
      return withSession(new FakeQuery((state) => one(store.messages.find((m) => String(m._id) === String(id)), state)), options)
    }) as never)
    sinon.stub(ChatSessionMessage, 'findOneAndReplace').callsFake(((filter: Filter, replacement: Doc, options?: SessionOption) => {
      assertSessionUsable(options)
      const index = store.messages.findIndex((m) => matchesFilter(plain(m), filter))
      if (index < 0) return new FakeQuery(() => null)
      const existing = store.messages[index] as MessageDoc
      const replaced = new ChatSessionMessage({ ...replacement, _id: existing._id })
      store.messages[index] = replaced
      store.writes.push('message.replace')
      return new FakeQuery((state) => one(replaced, state))
    }) as never)
    sinon.stub(ChatSessionMessage, 'findOneAndUpdate').callsFake(((filter: Filter, update: Doc, options?: SessionOption) => {
      assertSessionUsable(options)
      const doc = store.findMessages(filter)[0]
      if (doc) {
        applyUpdate(doc, update)
        store.writes.push('message.update')
      }
      return new FakeQuery((state) => one(doc, state))
    }) as never)

    const realCitationSave = Citation.prototype.save
    sinon.stub(Citation.prototype, 'save').callsFake(function (this: InstanceType<typeof Citation>, options?: SessionOption) {
      store.writes.push('citation.save')
      return realCitationSave.call(this, options as never)
    } as never)
    sinon.stub(Citation, 'updateMany').callsFake((() => {
      store.writes.push('citation.updateMany')
      return new FakeQuery(() => ({ modifiedCount: 0 }))
    }) as never)
    sinon.stub(Users, 'find').callsFake((() => new FakeQuery(() => [])) as never)
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  }
}

/**
 * A replica-set session that behaves like the driver's: usable inside and
 * after `withTransaction`, refused by every operation once `endSession()` ran.
 * A class instance, like the driver's, so Mongoose keeps it rather than cloning it.
 */
export class FakeReplicaSetSession {
  hasEnded = false
  withTransaction<T>(fn: () => Promise<T>): Promise<T> {
    return fn()
  }
  startTransaction(): void {}
  commitTransaction(): Promise<void> {
    return Promise.resolve()
  }
  abortTransaction(): Promise<void> {
    return Promise.resolve()
  }
  inTransaction(): boolean {
    return false
  }
  endSession(): Promise<void> {
    this.hasEnded = true
    return Promise.resolve()
  }
}

export const fakeReplicaSetSession = (): FakeReplicaSetSession => new FakeReplicaSetSession()

export interface SSEEvent {
  event: string
  data: Record<string, unknown>
}

/** The browser end of an SSE connection (and of a plain JSON reply). */
export class FakeSSEResponse extends EventEmitter {
  statusCode: number | undefined
  headersSent = false
  writableEnded = false
  writableFinished = false
  body = ''
  jsonBody: unknown
  writesAfterEnd = 0
  private resolveEnded: () => void = () => undefined
  readonly ended: Promise<void> = new Promise((resolve) => {
    this.resolveEnded = resolve
  })

  writeHead(statusCode: number): this {
    this.statusCode = statusCode
    this.headersSent = true
    return this
  }
  status(statusCode: number): this {
    this.statusCode = statusCode
    return this
  }
  json(body: unknown): this {
    this.jsonBody = body
    this.end()
    return this
  }
  write(chunk: string): boolean {
    if (this.writableEnded) this.writesAfterEnd += 1
    else this.body += chunk
    return true
  }
  end(): this {
    if (!this.writableEnded) {
      this.writableEnded = true
      this.writableFinished = true
      this.resolveEnded()
      this.emit('close')
    }
    return this
  }
  flush(): void {}

  /** The user closed the tab or hit stop: the socket goes away before we end the response. */
  disconnect(): void {
    this.emit('close')
  }

  events(): SSEEvent[] {
    return this.body
      .split('\n\n')
      .filter((frame) => frame.trim())
      .map((frame) => {
        const lines = frame.split('\n')
        const event = (lines.find((l) => l.startsWith('event:')) ?? '').replace('event:', '').trim()
        const data = lines
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.replace(/^data: ?/, ''))
          .join('\n')
        let parsed: Record<string, unknown>
        try {
          parsed = JSON.parse(data) as Record<string, unknown>
        } catch {
          parsed = { raw: data }
        }
        return { event, data: parsed }
      })
  }

  eventsOf(type: string): SSEEvent[] {
    return this.events().filter((e) => e.event === type)
  }
}

interface RecordedCall {
  url: string
  method: string
  body: Record<string, unknown>
}

type Reply = { kind: 'json'; status: number; body: unknown } | { kind: 'reject'; error: Error }

/**
 * The Python AI service, reached through the global `fetch` the controller's
 * AI command uses. Streaming calls get a live body the test writes frames to;
 * other calls get the reply registered for their path.
 */
export class FakeAIBackend {
  readonly calls: RecordedCall[] = []
  private controller: ReadableStreamDefaultController<Uint8Array> | null = null
  private streamReply: Reply | null = null
  private readonly replies: Array<{ path: RegExp; reply: Reply }> = []
  private readonly encoder = new TextEncoder()

  install(): void {
    sinon.stub(globalThis, 'fetch').callsFake((input: string | URL | Request, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input.toString()
      const rawBody = typeof init?.body === 'string' ? init.body : '{}'
      this.calls.push({ url, method: init?.method ?? 'GET', body: JSON.parse(rawBody) as Record<string, unknown> })
      const registered = this.replies.find((r) => r.path.test(url))
      const isStream = /\/stream(\?|$)/.test(url)
      const reply = registered?.reply ?? (isStream ? this.streamReply : null)
      if (reply?.kind === 'reject') return Promise.reject(reply.error)
      if (reply?.kind === 'json') {
        return Promise.resolve(
          new Response(JSON.stringify(reply.body), { status: reply.status, headers: { 'content-type': 'application/json' } }),
        )
      }
      if (!isStream) {
        return Promise.resolve(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
      }
      const body = new ReadableStream<Uint8Array>({
        start: (controller) => {
          this.controller = controller
        },
      })
      init?.signal?.addEventListener('abort', () => {
        this.controller?.error(new DOMException('This operation was aborted', 'AbortError'))
      })
      return Promise.resolve(new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }))
    })
  }

  /** Answer any request whose URL matches `path` with this JSON status and body. */
  reply(path: RegExp, status: number, body: unknown): void {
    this.replies.push({ path, reply: { kind: 'json', status, body } })
  }

  /** The next stream request is refused before any frame is sent. */
  refuseStream(status: number, body: unknown): void {
    this.streamReply = { kind: 'json', status, body }
  }

  /** The next requests fail at the network layer (service down or unreachable). */
  failNetwork(path: RegExp, error: Error): void {
    this.replies.push({ path, reply: { kind: 'reject', error } })
  }

  get streamCalls(): RecordedCall[] {
    return this.calls.filter((c) => /\/stream(\?|$)/.test(c.url))
  }

  sendRaw(text: string): void {
    this.controller?.enqueue(this.encoder.encode(text))
  }

  send(type: string, fields: Record<string, unknown> = {}): void {
    this.sendRaw(`event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`)
  }

  finish(): void {
    this.controller?.close()
  }

  breakConnection(error: Error): void {
    this.controller?.error(error)
  }
}

/** Lets queued stream chunks, listeners and saves run to completion. */
export const settle = async (rounds = 5): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setImmediate(resolve))
  }
}

export const oid = (): Types.ObjectId => new mongoose.Types.ObjectId()
