import { expect } from 'chai';
import sinon from 'sinon';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { READ_METHODS, idsOf, isReadMethod, pathsOf, shadowEnabled, shadowInFlight, shadowWrap, type RunPg } from '../../../src/libs/repositories/shadow';
import { Logger } from '../../../src/libs/services/logger.service';
import { docStore, pickStore } from '../../../src/libs/repositories/store-select';
import { docstoreMetrics } from '../../../src/libs/repositories/metrics';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const counts: Record<string, number> = {};
const realInc = docstoreMetrics.shadowReads.inc.bind(docstoreMetrics.shadowReads);

describe('shadow reads (DOC_STORE=shadow)', () => {
  const saved = { DOC_STORE: process.env.DOC_STORE, S: process.env.DOC_STORE_SHADOW_SAMPLE, T: process.env.DOC_STORE_SHADOW_SETTLE_MS, O: process.env.DOC_STORE_SHADOW_TIMEOUT_MS, M: process.env.DOC_STORE_SHADOW_MAX_IN_FLIGHT };
  const put = (k: string, v: string | undefined): void => { if (v === undefined) delete process.env[k]; else process.env[k] = v };
  before(() => {
    (docstoreMetrics.shadowReads as any).inc = (l: { repo: string; result: string }) => { counts[`${l.repo}:${l.result}`] = (counts[`${l.repo}:${l.result}`] ?? 0) + 1; };
    process.env.DOC_STORE_SHADOW_SAMPLE = '1';
    process.env.DOC_STORE_SHADOW_SETTLE_MS = '20';
    process.env.DOC_STORE_SHADOW_TIMEOUT_MS = '200';
  });
  after(() => {
    (docstoreMetrics.shadowReads as any).inc = realInc;
    put('DOC_STORE', saved.DOC_STORE); put('DOC_STORE_SHADOW_SAMPLE', saved.S); put('DOC_STORE_SHADOW_SETTLE_MS', saved.T); put('DOC_STORE_SHADOW_TIMEOUT_MS', saved.O); put('DOC_STORE_SHADOW_MAX_IN_FLIGHT', saved.M);
  });
  beforeEach(() => { for (const k of Object.keys(counts)) delete counts[k]; });

  const runPg: RunPg = (fn) => fn('pg-tx');

  it('DOC_STORE=shadow means Mongo is the store; other values are refused', () => {
    process.env.DOC_STORE = 'shadow';
    expect(docStore()).to.equal('mongo');
    expect(shadowEnabled()).to.equal(true);
    process.env.DOC_STORE = 'both';
    expect(() => docStore()).to.throw(/shadow/);
    delete process.env.DOC_STORE;
  });

  it('pickStore wraps the Mongo implementation under shadow and returns it as is otherwise', () => {
    const mongo = { findX: async () => 1 };
    const pg = { findX: async () => 1 };
    delete process.env.DOC_STORE;
    expect(pickStore('x', { mongo: () => mongo, postgres: () => pg })).to.equal(mongo);
    process.env.DOC_STORE = 'shadow';
    expect(pickStore('x', { mongo: () => mongo, postgres: () => pg })).to.not.equal(mongo);
    // a repository without a PostgreSQL implementation is not shadowed
    expect(pickStore('y', { mongo: () => mongo })).to.equal(mongo);
    delete process.env.DOC_STORE;
  });

  it('classifies reads by name', () => {
    for (const n of ['findById', 'getLiveById', 'listVisible', 'countMessages', 'existsLiveEmail', 'searchArchived', 'isAllowed']) expect(isReadMethod(n), n).to.equal(true);
    for (const n of ['create', 'update', 'delete', 'markRead', 'revoke', 'upsertX', 'issueAccess', 'issueRefresh']) expect(isReadMethod(n), n).to.equal(false);
  });

  it('every repository method that looks like a read is classified (a new one must be added to the list or named so it is not one)', () => {
    const root = join(__dirname, '../../../src/libs/repositories');
    const looksRead = /^(find|get|list|count|exists?|search|lookup|has|resolve|read|fetch|load|query|check|select|stats|page)/;
    const missing: string[] = [];
    for (const dir of readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory())) {
      let text: string;
      try { text = readFileSync(join(root, dir.name, 'types.ts'), 'utf8'); } catch { continue; }
      for (const m of text.matchAll(/^ {2}(?:readonly )?([a-zA-Z0-9_]+)(?:<[^(]*>)?\(/gm)) if (looksRead.test(m[1]) && !READ_METHODS.has(m[1])) missing.push(`${dir.name}.${m[1]}`);
    }
    expect(missing).to.deep.equal([]);
  });

  it('keeps a synchronous method synchronous (isConnected is a boolean, not a promise)', () => {
    const repo = shadowWrap('thing', { isConnected: () => false }, () => ({ isConnected: () => true }), runPg);
    expect(isReadMethod('isConnected')).to.equal(false);
    expect(repo.isConnected()).to.equal(false);
  });

  it('returns the Mongo answer, compares in the background, and counts an equal read', async () => {
    const calls: unknown[][] = [];
    const repo = shadowWrap('thing', { findById: async (id: string) => ({ _id: id, n: 1 }) }, () => ({ findById: async (...a: unknown[]) => (calls.push(a), { _id: a[0], n: 1 }) }), runPg);
    expect(await repo.findById('a1')).to.deep.equal({ _id: 'a1', n: 1 });
    await wait(50);
    expect(calls).to.deep.equal([['a1']]);
    expect(counts['thing:equal']).to.equal(1);
  });

  it('a difference is counted per repository and logged with ids and paths only', async () => {
    const logged: unknown[] = [];
    // Stub the class, not the shared logger instance: an own `warn` left on the instance would shadow every later stub.
    const warn = sinon.stub(Logger.prototype, 'warn').callsFake((_m: string, meta?: unknown) => {
      logged.push(meta);
    });
    try {
      const repo = shadowWrap('thing', { listAllLive: async () => [{ _id: 'a', email: 'secret@example.com' }] }, () => ({ listAllLive: async () => [{ _id: 'a', email: 'other@example.com' }] }), runPg);
      await repo.listAllLive();
      await wait(100);
    } finally { warn.restore(); }
    expect(counts['thing:diff']).to.equal(1);
    expect(JSON.stringify(logged)).to.not.match(/example\.com|secret/);
    expect(logged[0]).to.include({ repo: 'thing', method: 'listAllLive' });
    expect((logged[0] as any).ids).to.deep.equal(['a']);
    expect((logged[0] as any).fields).to.deep.equal(['$[0].email']);
  });

  it('a difference that settles within the delay is not reported', async () => {
    let state = 1;
    const repo = shadowWrap('thing', { getNames: async () => ({ _id: 'a', n: state }) }, () => ({ getNames: async () => ({ _id: 'a', n: 2 }) }), runPg);
    await repo.getNames();       // mongo says 1, pg says 2 ...
    state = 2;               // ... and mongo catches up before the re-check
    await wait(100);
    expect(counts['thing:equal']).to.equal(1);
    expect(counts['thing:diff']).to.equal(undefined);
  });

  it('never blocks or fails the caller: a slow PostgreSQL read times out, an error is counted, writes are untouched', async () => {
    const wrote: unknown[] = [];
    const repo = shadowWrap('thing', { findOpen: async () => 1, findMessage: async () => 2, create: async (x: unknown) => (wrote.push(x), 'ok') }, () => ({
      findOpen: () => wait(2000).then(() => 1), findMessage: async () => { throw new Error('boom'); }, create: async () => { throw new Error('must not run'); },
    }), runPg);
    const t0 = Date.now();
    expect(await repo.findOpen()).to.equal(1);
    expect(await repo.findMessage()).to.equal(2);
    expect(await repo.create('x')).to.equal('ok');
    expect(Date.now() - t0).to.be.lessThan(150);
    await wait(400);
    expect(counts['thing:timeout']).to.equal(1);
    expect(counts['thing:error']).to.equal(1);
    expect(wrote).to.deep.equal(['x']);
  });

  it('the sample rate and the in-flight cap bound the cost', async () => {
    process.env.DOC_STORE_SHADOW_SAMPLE = '0';
    let pgCalls = 0;
    const mk = () => shadowWrap('thing', { findOwned: async () => 1 }, () => ({ findOwned: async () => (pgCalls++, 1) }), runPg);
    await mk().findOwned();
    await wait(30);
    expect(pgCalls).to.equal(0);
    process.env.DOC_STORE_SHADOW_SAMPLE = '1';
    process.env.DOC_STORE_SHADOW_MAX_IN_FLIGHT = '2';
    const slow = shadowWrap('thing', { findOwned: async () => 1 }, () => ({ findOwned: async () => (pgCalls++, await wait(100), 1) }), runPg);
    await Promise.all([slow.findOwned(), slow.findOwned(), slow.findOwned(), slow.findOwned()]);
    expect(shadowInFlight()).to.be.at.most(2);
    await wait(250);
    expect(pgCalls).to.equal(2);
    delete process.env.DOC_STORE_SHADOW_MAX_IN_FLIGHT;
  });

  it('a read inside an open Mongo transaction is not compared; a Tx argument is replaced by the PostgreSQL one', async () => {
    let pgCalls = 0;
    const seen: unknown[] = [];
    const repo = shadowWrap('thing', { findOwned: async (...a: unknown[]) => a.length }, () => ({ findOwned: async (t: unknown) => (pgCalls++, seen.push(t), 1) }), runPg);
    await repo.findOwned({ store: 'mongo', session: { inTransaction: () => true } });
    await wait(30);
    expect(pgCalls).to.equal(0);
    await repo.findOwned({ store: 'mongo', session: undefined });
    await wait(30);
    expect(seen).to.deep.equal(['pg-tx']);
    // a call without a transaction goes straight to the repository, which opens its own read-only unit
    const direct: unknown[][] = [];
    const repo2 = shadowWrap('thing', { findOwned: async (...a: unknown[]) => a.length }, () => ({ findOwned: async (...a: unknown[]) => (direct.push(a), 1) }), (() => { throw new Error('no unit expected'); }) as never);
    await repo2.findOwned('org1');
    await wait(30);
    expect(direct).to.deep.equal([['org1']]);
  });

  it('idsOf and pathsOf read ids and paths, nothing else', () => {
    expect(idsOf({ _id: 'x', a: 1 })).to.deep.equal(['x']);
    expect(idsOf([{ _id: 1 }, { b: 2 }, { _id: 3 }])).to.deep.equal(['1', '3']);
    expect(idsOf('text')).to.deep.equal([]);
    expect(pathsOf(['$.a: 1 != 2', '$.b: absent in left'])).to.deep.equal(['$.a', '$.b']);
  });
});
