import { expect } from 'chai'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import * as ts from 'typescript'

/**
 * Source lints for code that runs in a unit of work:
 *  1. no network I/O inside a unit: the callback may re-run, and the connection is held while it waits;
 *  2. no catch-and-continue on a unique violation: PostgreSQL aborts the transaction, so use ON CONFLICT or tx.savepoint;
 *  3. no `SELECT *` / selectAll() on chat messages: the columns the viewer may not see must be named out.
 */
const SRC = join(__dirname, '../../../src')

const NETWORK =
  /^(fetch|axios|got|superagent)(\.|$)|^https?\.(request|get)$|\b(producer|messageProducer|eventProducer|syncEventProducer|kafka\w*|redis\w*|mailer|smtp\w*)\b\.\w+$|\.(publish|sendMail)$|\b\w*e?mail\w*\.send\w*$/i
const DUPLICATE =
  /UniqueConstraintError|23505|11000|duplicate key|isDuplicate\w*|isUnique\w*|\.name\s*===?\s*['"]Unique/i

const parse = (text: string) => ts.createSourceFile('x.ts', text, ts.ScriptTarget.Latest, true)

function isUowCall(c: ts.CallExpression): boolean {
  const e = c.expression
  if (ts.isIdentifier(e)) return /^(with(Jobs)?Uow|inUnit)$/.test(e.text)
  if (!ts.isPropertyAccessExpression(e)) return false
  if (/^with(Jobs)?Uow$/.test(e.name.text)) return true
  return e.name.text === 'run' && /uow|unitofwork/i.test(e.expression.getText())
}

/** A function passed by name (`uow.run(work)`): its declaration in the same file. */
function declared(sf: ts.SourceFile, name: string): ts.FunctionLikeDeclaration[] {
  const found: ts.FunctionLikeDeclaration[] = []
  const visit = (n: ts.Node) => {
    if (ts.isFunctionDeclaration(n) && n.name?.text === name) found.push(n)
    if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.name.text === name && n.initializer && (ts.isArrowFunction(n.initializer) || ts.isFunctionExpression(n.initializer))) found.push(n.initializer)
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return found
}

function callbacks(sf: ts.SourceFile): ts.FunctionLikeDeclaration[] {
  const out: ts.FunctionLikeDeclaration[] = []
  const visit = (n: ts.Node) => {
    if (ts.isCallExpression(n) && isUowCall(n)) {
      const inline = [...n.arguments].reverse().find((a) => ts.isArrowFunction(a) || ts.isFunctionExpression(a))
      if (inline) out.push(inline as ts.FunctionLikeDeclaration)
      else
        for (const a of n.arguments) {
          const found = ts.isIdentifier(a) ? declared(sf, a.text) : []
          if (found.length) {
            out.push(...found)
            break
          }
        }
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return out
}

const where = (sf: ts.SourceFile, n: ts.Node) => `line ${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`

export function networkInUnit(text: string): string[] {
  const sf = parse(text)
  const found: string[] = []
  for (const cb of callbacks(sf)) {
    const visit = (n: ts.Node) => {
      if (ts.isCallExpression(n) && NETWORK.test(n.expression.getText().replace(/^(globalThis|window|this)\./, ''))) found.push(`${where(sf, n)}: ${n.expression.getText()}`)
      if (ts.isNewExpression(n) && /Command$/.test(n.expression.getText())) found.push(`${where(sf, n)}: new ${n.expression.getText()}`)
      ts.forEachChild(n, visit)
    }
    if (cb.body) visit(cb.body)
  }
  return found
}

export function swallowedDuplicate(text: string): string[] {
  const sf = parse(text)
  const found: string[] = []
  for (const cb of callbacks(sf)) {
    const visit = (n: ts.Node) => {
      if (ts.isTryStatement(n) && n.catchClause && DUPLICATE.test(n.catchClause.getText()) && !/\.savepoint\(/.test(n.tryBlock.getText()))
        found.push(`${where(sf, n)}: catch of a unique violation inside a unit`)
      if (
        ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && n.expression.name.text === 'catch' &&
        n.arguments.some((a) => DUPLICATE.test(a.getText())) && !/\.savepoint\(/.test(n.expression.expression.getText())
      )
        found.push(`${where(sf, n)}: .catch of a unique violation inside a unit`)
      ts.forEachChild(n, visit)
    }
    if (cb.body) visit(cb.body)
  }
  return found
}

export function selectStarOnChatMessages(text: string): string[] {
  const sf = parse(text)
  const found: string[] = []
  const visit = (n: ts.Node) => {
    if ((ts.isStringLiteralLike(n) || ts.isTemplateExpression(n)) && /chat_messages/i.test(n.getText()) && /select\s+(\w+\.)?\*|returning\s+\*|row_to_json|to_jsonb/i.test(n.getText()))
      found.push(`${where(sf, n)}: SELECT * on chat_messages`)
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && /^(selectAll|returningAll)$/.test(n.expression.name.text)) {
      let top: ts.Node = n
      while (top.parent && !ts.isExpressionStatement(top.parent) && !ts.isVariableStatement(top.parent) && !ts.isReturnStatement(top.parent) && !ts.isBlock(top.parent)) top = top.parent
      if (/chat_messages/i.test(top.getText()) || /chat_messages/i.test(text)) found.push(`${where(sf, n)}: selectAll() in a file that reads chat_messages`)
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return found
}

/** Mongo write paths the store fence does not wrap; the application must not use them. */
export function unfencedMongoWrites(text: string): string[] {
  const sf = parse(text)
  const found: string[] = []
  const visit = (n: ts.Node) => {
    if (ts.isPropertyAccessExpression(n) && /^initialize(Un)?orderedBulkOp$/i.test(n.name.text)) found.push(`${where(sf, n)}: ${n.name.text}`)
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)) {
      const m = n.expression.name.text
      const recv = n.expression.expression.getText()
      if (m === 'command' && /db|connection|admin|client/i.test(recv)) found.push(`${where(sf, n)}: ${recv}.command`)
      if (m === 'bulkWrite' && /client/i.test(recv)) found.push(`${where(sf, n)}: client.bulkWrite`)
    }
    if ((ts.isStringLiteralLike(n) || ts.isTemplateExpression(n)) && /['"`]\$(merge|out)['"`]/.test(n.getText())) found.push(`${where(sf, n)}: $merge/$out`)
    if (ts.isIdentifier(n) && /^\$?(merge|out)$/.test(n.text) && ts.isPropertyAssignment(n.parent) && n.parent.name === n && /^\$/.test(n.text)) found.push(`${where(sf, n)}: ${n.text}`)
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return found
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) return f === 'generated' ? [] : sourceFiles(p)
    return p.endsWith('.ts') ? [p] : []
  })
}

describe('unit-of-work source lints', () => {
  describe('detectors', () => {
    it('finds network calls inside a unit callback and ignores the same call outside it', () => {
      const bad = `
        await uow.run(async (tx) => { await tx.x(); await fetch('http://a'); await this.messageProducer.publish(1); await axios.get('u'); const r = new AIServiceCommand({}); });
        await withUow({}, async () => { await kafkaProducer.send({}) });
        await fetch('ok-outside')`
      const found = networkInUnit(bad)
      expect(found).to.have.length(5)
      expect(networkInUnit(`await fetch('x'); await uow.run(async (tx) => { await tx.query('SELECT 1') })`)).to.deep.equal([])
    })

    it('finds a caught unique violation inside a unit, but not around a savepoint', () => {
      expect(swallowedDuplicate(`await uow.run(async (tx) => { try { await repo.create(x, tx) } catch (e) { if (e instanceof UniqueConstraintError) return existing } })`)).to.have.length(1)
      expect(swallowedDuplicate(`await withUow({}, async (tx) => { await tx.query(q).catch((e) => { if (e.code === '23505') return }) })`)).to.have.length(1)
      expect(swallowedDuplicate(`await uow.run(async (tx) => { try { await tx.savepoint('s', () => repo.create(x, tx)) } catch (e) { if (e instanceof UniqueConstraintError) return } })`)).to.deep.equal([])
      expect(swallowedDuplicate(`try { await repo.create(x) } catch (e) { if (e instanceof UniqueConstraintError) return }`)).to.deep.equal([])
    })

    it('treats the repositories’ own unit helper like a unit of work', () => {
      expect(networkInUnit(`await inUnit(tx, { write: true }, async (k) => { await fetch('http://a') })`)).to.have.length(1)
      expect(swallowedDuplicate(`await inUnit(undefined, { write: true }, async (k) => { try { await k.query(q) } catch (e) { if (e instanceof UniqueConstraintError) return } })`)).to.have.length(1)
    })

    it('follows a callback passed by name, and the usual disguises', () => {
      expect(networkInUnit(`async function work(tx) { await globalThis.fetch('u') }\nawait uow.run(work)`)).to.have.length(1)
      expect(networkInUnit(`const work = async () => { await emailService.sendEmail({}) }\nawait withUow({}, work)`)).to.have.length(1)
      expect(swallowedDuplicate(`await uow.run(async () => { try { await x() } catch (e) { if (isDuplicateKey(e)) return } })`)).to.have.length(1)
      expect(swallowedDuplicate(`await uow.run(async () => { try { await x() } catch (e) { if (e.name === 'UniqueConstraintError') return } })`)).to.have.length(1)
    })

    it('finds more ways to read every chat message column', () => {
      expect(selectStarOnChatMessages('const q = "INSERT INTO conversation.chat_messages (id) VALUES ($1) RETURNING *"')).to.have.length(1)
      expect(selectStarOnChatMessages('const q = "SELECT row_to_json(m) FROM conversation.chat_messages m"')).to.have.length(1)
      expect(selectStarOnChatMessages("const t = 'conversation.chat_messages'; const q = db.selectFrom(t);\nconst r = await q.selectAll().execute()")).to.have.length(1)
      expect(selectStarOnChatMessages("await db.insertInto('conversation.chat_messages').values(v).returningAll().execute()")).to.have.length(1)
    })

    it('finds Mongo write paths the fence does not wrap', () => {
      expect(unfencedMongoWrites('const b = col.initializeUnorderedBulkOp(); await db.command({ insert: "x" }); await client.bulkWrite([]); col.aggregate([{ $merge: "x" }, { $out: "y" }])')).to.have.length(5)
      expect(unfencedMongoWrites('await conn.db.command({ ping: 1 })')).to.have.length(1)
      expect(unfencedMongoWrites('await col.updateOne({}, {}); await col.aggregate([{ $match: {} }])')).to.deep.equal([])
    })

    it('finds SELECT * and selectAll() on chat messages only', () => {
      expect(selectStarOnChatMessages('const q = "SELECT * FROM conversation.chat_messages WHERE id = $1"')).to.have.length(1)
      expect(selectStarOnChatMessages('const q = `select m.* from conversation.chat_messages m`')).to.have.length(1)
      expect(selectStarOnChatMessages("const rows = await db.selectFrom('conversation.chat_messages').selectAll().execute()")).to.have.length(1)
      expect(selectStarOnChatMessages("const rows = await db.selectFrom('platform.notifications').selectAll().execute()")).to.deep.equal([])
      expect(selectStarOnChatMessages('const q = "SELECT id, role FROM conversation.chat_messages"')).to.deep.equal([])
    })
  })

  describe('the source tree', () => {
    const files = sourceFiles(SRC).map((f) => [relative(SRC, f), readFileSync(f, 'utf8')] as const)
    it('sees the units of work in the tree (the lints are not vacuous)', () => {
      expect(files.reduce((n, [, t]) => n + callbacks(parse(t)).length, 0)).to.be.greaterThan(5)
    })
    it('has no network I/O inside a unit of work', () => {
      expect(files.flatMap(([f, t]) => networkInUnit(t).map((m) => `${f} ${m}`))).to.deep.equal([])
    })
    it('catches no unique violation inside a unit and carries on', () => {
      expect(files.flatMap(([f, t]) => swallowedDuplicate(t).map((m) => `${f} ${m}`))).to.deep.equal([])
    })
    it('uses no Mongo write path that the store fence does not wrap', () => {
      const own = new Set(['libs/services/mongo.service.ts', 'libs/repositories/fence.ts', 'libs/repositories/mongo.ts', 'modules/configuration_manager/services/migrations/delegation_log_retention.migration.ts']) // mongo.ts only kills its own session; the migration's collMod changes an index's TTL, not data
      // the migration tool owns the fence and uses the raw driver: its writers (fence document, drain, reverse copy) and its read-only
      // admin commands (hello, replSetGetStatus in the preflight, the follower and the cut's pre-checks; ping for the freeze's cluster time) are listed by file; nothing else in it is exempt
      const migrator = new Set(['fence.ts', 'drain.ts', 'reverse.ts', 'reverse-special.ts', 'reverse-follow.ts', 'preflight.ts', 'follow.ts', 'online.ts'].map((f) => `scripts/docstore-migrate/${f}`))
      // The document repository reads the server clock with the read-only hello (or isMaster) admin
      // command; only those two calls are allowed there, so any other unfenced write in it still fails.
      const clockReads = (f: string, m: string) => f === 'libs/repositories/document/mongo.ts' && /: admin\.command$/.test(m)
      const clock = files.flatMap(([f, t]) => unfencedMongoWrites(t).filter((m) => clockReads(f, m)))
      expect(clock).to.have.length(2)
      expect(files.filter(([f]) => !own.has(f) && !migrator.has(f)).flatMap(([f, t]) => unfencedMongoWrites(t).filter((m) => !clockReads(f, m)).map((m) => `${f} ${m}`))).to.deep.equal([])
    })
    it('has no SELECT * on chat messages', () => {
      expect(files.flatMap(([f, t]) => selectStarOnChatMessages(t).map((m) => `${f} ${m}`))).to.deep.equal([])
    })
  })
})
