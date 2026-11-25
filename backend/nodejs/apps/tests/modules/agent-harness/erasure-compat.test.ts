import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { readFileSync } from 'fs';
import { join } from 'path';
import { sessionErasures } from '../../../src/modules/agent-harness/composition';
import { AGENT_HARNESS_SESSION_ERASURES } from '../../../src/modules/agent-harness/binders/tokens';
import {
  erasureMayRun,
  erasureOnDelete,
  orgErasureJobsAvailable,
  recordOrgHarnessReader,
} from '../../../src/modules/agent-harness/config';

/**
 * R51-COMPAT item 11 (ADR-OPS-07): with the harness off on the compatibility profile, deleting a chat is the soft
 * delete a release without E7 did; erasure is enqueued where the harness is on or the install is hardened.
 */
describe('R51-COMPAT: conversation erasure follows the harness switch or the hardened profile', () => {
  const queue = { enqueue: async () => undefined };
  const bound = (): Container => {
    const container = new Container();
    container.bind(AGENT_HARNESS_SESSION_ERASURES).toConstantValue(queue);
    return container;
  };

  afterEach(() => {
    recordOrgHarnessReader(() => Promise.resolve(true));
  });

  it('erases on delete only for an org with the harness on, or on the hardened profile (ADR-OPS-08)', async () => {
    expect(await erasureOnDelete('org-1', { AGENT_HARNESS_WORKER_ENABLED: 'false' })).to.equal(false);
    expect(await erasureOnDelete('org-1', { AGENT_HARNESS_WORKER_ENABLED: 'true' })).to.equal(true);
    expect(await erasureOnDelete('org-1', { AGENT_HARNESS_WORKER_ENABLED: 'false', PIPESHUB_HARDENED: 'true' })).to.equal(true);
    recordOrgHarnessReader(() => Promise.resolve(false));
    // The switch on and the org's Labs off is the switch off: a soft delete on the compatibility profile.
    expect(await erasureOnDelete('org-1', { AGENT_HARNESS_WORKER_ENABLED: 'true' })).to.equal(false);
    expect(await erasureOnDelete('org-1', { AGENT_HARNESS_WORKER_ENABLED: 'true', PIPESHUB_HARDENED: 'true' })).to.equal(true);
    expect(erasureMayRun({ AGENT_HARNESS_WORKER_ENABLED: 'true' })).to.equal(true);
    expect(erasureMayRun({ AGENT_HARNESS_WORKER_ENABLED: 'false' })).to.equal(false);
  });

  it('gives the delete routes no erasure queue with the harness off on the compatibility profile', () => {
    expect(sessionErasures(bound(), { AGENT_HARNESS_WORKER_ENABLED: 'false' })).to.equal(undefined);
    expect(sessionErasures(bound(), { AGENT_HARNESS_WORKER_ENABLED: 'false', PIPESHUB_HARDENED: 'true' })).to.equal(queue);
    expect(sessionErasures(bound(), { AGENT_HARNESS_WORKER_ENABLED: 'true' })).to.equal(queue);
  });

  it('without a queue the delete is the soft delete alone (no erasure mark or job)', () => {
    const source = readFileSync(
      join(__dirname, '../../../src/modules/enterprise_search/controller/es_controller.ts'),
      'utf8',
    );
    expect(source).to.include('if (deleted && erasures !== undefined) {');
  });

  it('R52SPLITA-FU-1: an org delete gets its erasure job wherever a delete erases, harness on or a hardened install with it off', () => {
    const rs = { REPLICA_SET_AVAILABLE: 'true' };
    expect(orgErasureJobsAvailable({ ...rs, AGENT_HARNESS_WORKER_ENABLED: 'false', PIPESHUB_HARDENED: 'true' }, null)).to.equal(true);
    expect(orgErasureJobsAvailable({ ...rs, AGENT_HARNESS_WORKER_ENABLED: 'true' }, null)).to.equal(true);
    expect(orgErasureJobsAvailable({ DOC_STORE: 'postgres', PIPESHUB_HARDENED: 'true' }, null)).to.equal(true);
    // The compatibility profile with the harness off keeps upstream's soft delete: no job.
    expect(orgErasureJobsAvailable({ ...rs, AGENT_HARNESS_WORKER_ENABLED: 'false' }, null)).to.equal(false);
    // A job needs the store's transactions, and a durable journal where one was probed.
    expect(orgErasureJobsAvailable({ PIPESHUB_HARDENED: 'true' }, null)).to.equal(false);
    expect(orgErasureJobsAvailable({ ...rs, PIPESHUB_HARDENED: 'true' }, { durable: false } as never)).to.equal(false);
  });

  it('R52SPLITA-FU-1: the org controller is given that job service even where user deletion has none, and the sweeper that runs it starts on every install', () => {
    const container = readFileSync(
      join(__dirname, '../../../src/modules/user_management/container/userManager.container.ts'),
      'utf8',
    );
    expect(container).to.include('(orgErasureJobsAvailable() ? new PrincipalDeletionService() : null)');
    expect(container).to.match(/orgQueryService,\s*orgPrincipalDeletion,\s*\)/);
    const es = readFileSync(
      join(__dirname, '../../../src/modules/enterprise_search/container/es.container.ts'),
      'utf8',
    );
    expect(es).to.include('await startSessionErasureServices(');
  });
});
