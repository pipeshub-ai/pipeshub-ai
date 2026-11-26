/**
 * The control plane's Authorizer for tests of the admin routes: the org role
 * is whatever `isUserOrgAdmin` answers when asked, so a test that stubs it
 * (as the routes' tests always have) decides who is an admin (CPD-11 slice B).
 */
import type { Container } from 'inversify';
import { CONTROL_PLANE_AUTHORIZER } from '../../../../src/modules/control-plane';
import { RoleAuthorizer } from '../../../../src/modules/control-plane/application/role-authorizer';
import { UnreadableDirectory } from '../../../../src/modules/control-plane/adapters/unreadable-directory';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';

export function bindAdminAuthorizer(container: Container): void {
  if (container.isBound(CONTROL_PLANE_AUTHORIZER)) return;
  const directory = new UnreadableDirectory();
  container.bind(CONTROL_PLANE_AUTHORIZER).toConstantValue(
    new RoleAuthorizer(
      {
        roleOf: async (orgId, userId) => ((await userAdminService.isUserOrgAdmin(userId, orgId)) ? 'admin' : 'member'),
      },
      directory,
      directory,
      { delegatedActions: async () => [] },
    ),
  );
}
