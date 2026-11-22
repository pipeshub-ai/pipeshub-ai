/**
 * The real bulk admin invite (UserController.processInvites) against a real user store, for the store suites that race
 * it. Only the user and credential stores are real: groups, activity rows, mail and events are no-ops, since none of
 * them grants a role.
 */
import { UserController } from '../../src/modules/user_management/controller/users.controller';
import { Logger } from '../../src/libs/services/logger.service';
import { setGroupRepository } from '../../src/libs/repositories/group';
import { setActivityRepository } from '../../src/libs/repositories/activity';

type Invite = (orgId: string, emails: string[]) => Promise<unknown>;

/** Installs no-op group and activity stores; returns the function that puts the defaults back. */
export function withoutSideStores(): () => void {
  setGroupRepository({
    addToEveryone: async () => undefined,
    addMemberToGroups: async () => undefined,
  } as never);
  setActivityRepository({ recordMany: async () => undefined } as never);
  return () => {
    setGroupRepository();
    setActivityRepository();
  };
}

/** `invite(orgId, emails)` runs an admin invite of `emails` the way `POST /users/bulk/invite` does. */
export function adminInviter(rsAvailable: boolean): Invite {
  const config = {
    scopedJwtSecret: 'admin-invite-race-secret',
    frontendUrl: 'http://app.admin-invite-race.test',
    rsAvailable: String(rsAvailable),
  };
  const mail = { sendMail: async () => ({ statusCode: 200 }) };
  const auth = {
    passwordMethodEnabled: async () => ({
      statusCode: 200,
      data: { isPasswordAuthEnabled: false },
    }),
  };
  const events = {
    start: async () => undefined,
    stop: async () => undefined,
    publishEvent: async () => undefined,
  };
  const controller = new UserController(
    ...([
      config,
      mail,
      auth,
      Logger.getInstance({ service: 'admin-invite-race' }),
      events,
      {},
    ] as unknown as ConstructorParameters<typeof UserController>),
  ) as unknown as {
    processInvites(...args: unknown[]): Promise<unknown>;
  };
  return (orgId, emails) =>
    controller.processInvites(
      emails,
      undefined,
      orgId,
      'Inviter',
      { registeredName: 'Race Org' },
      'admin',
    );
}
