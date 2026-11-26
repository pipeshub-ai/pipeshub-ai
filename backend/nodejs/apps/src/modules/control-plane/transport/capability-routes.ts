import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import type { AdminPinServices, GrantAdmin } from '../application/grant-admin';
import {
  CONTROL_PLANE_GRANT_ADMIN,
  CONTROL_PLANE_PIN_SERVICES,
} from './admin-keys';
import { cpRoute } from './cp-route';

const READ = [OAuthScopeNames.CONFIG_READ];
const key = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/);

/** R22 of CPD-11 §3: what the agent builder shows its author of each installation the agent could use. */
export const CAPABILITY_ROUTES = [
  cpRoute({
    id: 'R22',
    method: 'get',
    path: '/agents/:agentKey/grantable',
    scopes: READ,
    params: z.object({ agentKey: key }),
    authorize: (ctx) => ({
      action: 'grant.write',
      resource: { type: 'grant', owner: `agent:${ctx.params.agentKey}` },
    }),
    async handle(ctx) {
      const items = await ctx
        .resolve<GrantAdmin>(CONTROL_PLANE_GRANT_ADMIN)
        .grantable(
          ctx.resolve<AdminPinServices>(CONTROL_PLANE_PIN_SERVICES),
          ctx.actor.orgId,
          ctx.actor.userId,
          ctx.params.agentKey,
        );
      return { body: { items } };
    },
  }),
];
