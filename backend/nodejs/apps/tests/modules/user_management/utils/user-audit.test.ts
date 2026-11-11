import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  USER_AUDIT_REASONS,
  auditMemberships,
  auditUserChange,
  foundGroupIds,
} from '../../../../src/modules/user_management/utils/user-audit';
import { Logger, SNAKE_TOKEN } from '../../../../src/libs/services/logger.service';
import * as seam from '../../../../src/libs/services/structured-logging';

const USER = '64f0c0ffee0000000000000b';
const GROUP = '64f0c0ffee0000000000000d';

describe('user-audit', () => {
  afterEach(() => {
    sinon.restore();
    (Logger as any).instance = null;
  });

  it('every reason is a snake_case code', () => {
    for (const reasons of Object.values(USER_AUDIT_REASONS)) {
      for (const reason of reasons) expect(reason).to.match(SNAKE_TOKEN);
    }
  });

  it("the lists updateUser's catch uses together are disjoint, so a refusal writes one line", () => {
    const email = new Set<string>(USER_AUDIT_REASONS['user.email_change.request']);
    for (const event of ['user.role.change', 'user.update'] as const) {
      for (const reason of USER_AUDIT_REASONS[event]) expect(email.has(reason), `${event}: ${reason}`).to.equal(false);
    }
  });

  for (const [name, write, event] of [
    ['auditUserChange', () => auditUserChange('user.delete', USER), 'user.delete'],
    ['auditMemberships', () => auditMemberships('group.member.add', [USER], [GROUP], new Set([GROUP])), 'group.member.add'],
  ] as const) {
    it(`${name} shows the target as a user, so a provider that hides user ids hides it`, () => {
      const provider = Object.assign(new seam.DefaultLoggingProvider(), {
        fileLoggingEnabled: () => false,
        logIdentity: (ids: Record<string, unknown>) => ({ ...ids, user_id: 'hashed-user' }),
      });
      sinon.stub(seam, 'getProvider').callsFake(() => provider);
      (Logger as any).instance = null;
      const log = sinon.stub((Logger.getInstance() as any).logger, 'log');
      write();
      expect(log.lastCall.args[0]).to.include({ message: event, target: 'hashed-user', outcome: 'success' });
      expect(JSON.stringify(log.lastCall.args[0])).to.not.include(USER);
    });
  }

  it('a line written through the wrapper names its caller, not user-audit.ts', () => {
    const provider = Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false });
    sinon.stub(seam, 'getProvider').callsFake(() => provider);
    (Logger as any).instance = null;
    const log = sinon.stub((Logger.getInstance() as any).logger, 'log');
    auditUserChange('user.delete', USER);
    expect(String(log.lastCall.args[0].filename)).to.equal('user-audit.test.ts');
  });

  it('foundGroupIds looks each distinct id up once and never looks up a value that is not an id', async () => {
    const find = sinon.stub().callsFake(async (id: string) => (id === GROUP ? { _id: id } : null));
    const found = await foundGroupIds([GROUP, 'not-an-id', GROUP, '64f0c0ffee0000000000000e'], find);
    expect([...found]).to.deep.equal([GROUP]);
    expect(find.args.map((a) => a[0])).to.deep.equal([GROUP, '64f0c0ffee0000000000000e']);
  });
});
