import 'reflect-metadata';
import { expect } from 'chai';
import { readdirSync } from 'fs';
import { join } from 'path';
import { actorOnlyApprovers } from '../../../src/modules/agent-harness/ports/approver-directory';
import { noRunWakeup } from '../../../src/modules/agent-harness/ports/run-wakeup';
import { ForkAlreadyCommittedError } from '../../../src/modules/agent-harness/ports/session-forks';
import { RecordAccessUnavailableError } from '../../../src/modules/agent-harness/ports/record-access';

const PORTS = join(__dirname, '../../../src/modules/agent-harness/ports');

/**
 * Ports are contracts (ARCHITECTURE 3.1 principle 7): at run time a port
 * module holds only its errors, DI tokens and the null objects named here.
 * Anything else exported from `ports/` is an implementation in the wrong place.
 */
const RUNTIME_EXPORTS: Record<string, readonly string[]> = {
  'agent-erasures': ['AGENT_DELETION_STATES'],
  'approval-rules': ['usualApprovalRules'],
  'approver-directory': ['actorOnlyApprovers'],
  'chat-path-metrics': ['noChatPathMetrics'],
  'cost-ledger': ['noSharedBudgets', 'noCostEraser'],
  'data-eraser-extension': ['DATA_ERASER_EXTENSION', 'noDataEraserExtension'],
  'journal-store': ['validatingPreparer'],
  'memory-store': ['AGENT_HARNESS_MEMORY_SETTINGS', 'MEMORY_ERASER'],
  'engine-profiles': ['noEngineCeilings'],
  'engine-signins': ['ENGINE_SIGNIN_ERASER', 'everyOrgAllowsSignIns'],
  inbox: ['INBOX_ERASER'],
  'model-accounts': ['MODEL_ACCOUNT_ERASER'],
  'personal-instances': ['PERSONAL_INSTANCE_ERASER'],
  'rated-answers': ['noRatedAnswerSink'],
  'record-access': ['RecordAccessUnavailableError'],
  'run-ready': ['noRunReady'],
  'run-wakeup': ['noRunWakeup'],
  schedules: ['SCHEDULE_ERASER'],
  'session-forks': ['ForkAlreadyCommittedError'],
  'session-retention': ['NO_RETENTION'],
};

const portModules = (): string[] =>
  readdirSync(PORTS)
    .filter((file) => file.endsWith('.ts'))
    .map((file) => file.slice(0, -'.ts'.length))
    .sort();

describe('agent-harness ports', () => {
  it('every port module loads and exports at run time only what the contract list names', () => {
    const modules = portModules();
    expect(modules.length).to.be.greaterThan(30);
    for (const name of modules) {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const loaded = require(join(PORTS, name)) as Record<string, unknown>;
      const exported = Object.keys(loaded).filter((key) => key !== '__esModule').sort();
      expect(exported, name).to.deep.equal([...(RUNTIME_EXPORTS[name] ?? [])].sort());
    }
  });

  it('lists no module that is not a port', () => {
    const modules = new Set(portModules());
    for (const name of Object.keys(RUNTIME_EXPORTS)) {
      expect(modules.has(name), name).to.equal(true);
    }
  });

  it('actorOnlyApprovers: nobody besides the actor approves, and nobody is an org admin', async () => {
    expect(await actorOnlyApprovers.isApprover('org-1', 'user-1')).to.equal(false);
    expect(await actorOnlyApprovers.isOrgAdmin('org-1', 'user-1')).to.equal(false);
  });

  it('noRunWakeup resolves without waking anything', async () => {
    expect(await noRunWakeup.wake('run-1')).to.equal(undefined);
  });

  it('the port errors carry their own names and reasons', () => {
    const committed = new ForkAlreadyCommittedError();
    expect(committed).to.be.instanceOf(Error);
    expect([committed.name, committed.message]).to.deep.equal([
      'ForkAlreadyCommittedError',
      'This fork was already committed',
    ]);
    const unavailable = new RecordAccessUnavailableError('unavailable', 'down');
    expect([unavailable.name, unavailable.reason]).to.deep.equal([
      'RecordAccessUnavailableError',
      'unavailable',
    ]);
  });
});
