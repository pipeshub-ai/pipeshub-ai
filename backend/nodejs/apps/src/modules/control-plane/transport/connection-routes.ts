import { z } from 'zod';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import type { BindingService } from '../application/binding-service';
import type { InstallationAdmin } from '../application/installation-admin';
import type { Binding, BindingOwnerRef } from '../domain/objects';
import type { Authorizer } from '../ports/authorizer';
import type { SecretMaterial } from '../ports/credential-binder';
import type { WriteContext } from '../ports/transaction';
import {
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_INSTALLATION_ADMIN,
} from './admin-keys';
import { CONTROL_PLANE_BINDINGS } from './binding-routes';
import { cpRoute, type CpRequestContext } from './cp-route';

const owner = z
  .string()
  .regex(/^(?:org|(?:agent|user):[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$/);
const bindingId = z.string().regex(/^bnd_[0-9A-HJKMNP-TV-Z]{26}$/);
/** A typed secret: an API key, or the header name and value an MCP server or toolset takes. */
const secret = z.discriminatedUnion('mode', [
  z
    .object({ mode: z.literal('api_key'), apiKey: z.string().min(1).max(4096) })
    .strict(),
  z
    .object({
      mode: z.literal('header'),
      name: z.string().regex(/^[A-Za-z0-9-]{1,128}$/),
      value: z.string().min(1).max(4096),
    })
    .strict(),
]);

const materialOf = (body: z.infer<typeof secret>): SecretMaterial =>
  body.mode === 'api_key'
    ? { kind: 'api_key' as const, value: { apiKey: body.apiKey } }
    : {
        kind: 'header' as const,
        value: { name: body.name, value: body.value },
      };

const service = (
  ctx: CpRequestContext<unknown, unknown, unknown>,
): BindingService => ctx.resolve<BindingService>(CONTROL_PLANE_BINDINGS);

const self = (
  ctx: CpRequestContext<unknown, unknown, unknown>,
): BindingOwnerRef => `user:${ctx.actor.userId}`;

/** The caller's own bindings; another owner's (the org's, an agent's) when the Authorizer lets them change it. */
const mayHandle =
  (ctx: CpRequestContext<unknown, unknown, unknown>) =>
  async (binding: Pick<Binding, 'owner'>): Promise<boolean> =>
    binding.owner === self(ctx) ||
    (
      await ctx
        .resolve<Authorizer>(CONTROL_PLANE_AUTHORIZER)
        .check(ctx.actor, 'binding.update', {
          orgId: ctx.actor.orgId,
          type: 'binding',
          owner: binding.owner,
        })
    ).allow;

const writer = (
  ctx: CpRequestContext<unknown, unknown, unknown>,
): WriteContext => ({
  orgId: ctx.actor.orgId,
  actor: `user:${ctx.actor.userId}`,
});

/**
 * R14–R16 ("My connections", 02-lld §9.1): list, connect with a typed
 * secret, rotate and revoke. The secret goes straight to the vault and is
 * never answered. Someone else's binding is not found, never forbidden.
 */
export const CONNECTION_ROUTES = [
  cpRoute({
    id: 'R14',
    method: 'get',
    path: '/bindings',
    scopes: [OAuthScopeNames.CONFIG_READ],
    query: z
      .object({ owner: z.union([z.literal('me'), owner]).default('me') })
      .strict(),
    authorize: () => 'in-handler',
    async handle(ctx) {
      const target = ctx.query.owner === 'me' ? self(ctx) : ctx.query.owner;
      if (!(await mayHandle(ctx)({ owner: target }))) {
        await ctx.authorize('binding.update', {
          type: 'binding',
          owner: target,
        });
      }
      return {
        body: { bindings: await service(ctx).list(ctx.actor.orgId, target) },
      };
    },
  }),
  cpRoute({
    id: 'R15',
    method: 'post',
    path: '/bindings',
    scopes: [OAuthScopeNames.CONFIG_WRITE],
    audited: true,
    body: z
      .object({
        installation: z.string().min(1).max(256),
        owner: z.union([z.literal('me'), owner]).default('me'),
        secret,
      })
      .strict(),
    authorize: (ctx) => ({
      action: 'binding.create',
      resource: {
        type: 'binding',
        owner: ctx.body.owner === 'me' ? self(ctx) : ctx.body.owner,
      },
    }),
    async handle(ctx) {
      const inst = await ctx
        .resolve<InstallationAdmin>(CONTROL_PLANE_INSTALLATION_ADMIN)
        .get(ctx.actor.orgId, ctx.body.installation);
      const binding = await service(ctx).create(
        writer(ctx),
        inst,
        ctx.body.owner === 'me' ? self(ctx) : ctx.body.owner,
        ctx.body.secret.mode,
        materialOf(ctx.body.secret),
      );
      return { status: 201, body: { binding }, etag: binding.revision };
    },
  }),
  cpRoute({
    id: 'R16-revoke',
    method: 'post',
    path: '/bindings/:bindingId/actions/revoke',
    scopes: [OAuthScopeNames.CONFIG_WRITE],
    audited: true,
    revision: 'required',
    params: z.object({ bindingId }),
    authorize: () => 'in-handler',
    async handle(ctx) {
      const binding = await service(ctx).revoke(
        writer(ctx),
        ctx.params.bindingId,
        ctx.ifMatch ?? 0,
        mayHandle(ctx),
      );
      return { body: { binding }, etag: binding.revision };
    },
  }),
  cpRoute({
    id: 'R16-rotate',
    method: 'post',
    path: '/bindings/:bindingId/actions/rotate',
    scopes: [OAuthScopeNames.CONFIG_WRITE],
    audited: true,
    revision: 'required',
    params: z.object({ bindingId }),
    body: z.object({ secret }).strict(),
    authorize: () => 'in-handler',
    async handle(ctx) {
      const binding = await service(ctx).rotate(
        writer(ctx),
        ctx.params.bindingId,
        ctx.ifMatch ?? 0,
        materialOf(ctx.body.secret),
        mayHandle(ctx),
      );
      return { body: { binding }, etag: binding.revision };
    },
  }),
];
