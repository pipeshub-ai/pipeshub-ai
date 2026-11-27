import { expect } from 'chai';
import { RequestHandler, Router } from 'express';
import {
  CONFIG_ROUTER_SPEC,
  IdentityControlSpec,
  USER_ROUTER_SPEC,
  identityControlViolations,
  routeChains,
} from '../../helpers/session-only-routes';
import {
  isAdminRole,
  requireSessionAuth,
  requireSessionAuthExcept,
  requireSessionAuthForFields,
} from '../../../src/libs/middlewares/require-session-auth.middleware';
import { requireScopes } from '../../../src/libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum';

// Self-test of the route scan: a router table that is right passes, and each way of getting it
// wrong (applied in memory to the table) makes the scan report something.

const authenticate = function authenticate() {
  /* stand-in */
};
const scope = function scope() {
  /* stand-in for a scope check */
};
const admin = function admin() {
  /* stand-in for an admin check */
};
const handler = function handler() {
  /* stand-in */
};
const emailAndRole = requireSessionAuthForFields({
  email: true,
  role: isAdminRole,
});
const roleOnly = requireSessionAuthForFields({ role: isAdminRole });
const noopGuard = function requireSessionAuth() {
  /* same name, not the real guard */
};

type Table = Array<{
  method: string;
  path: string;
  handlers: RequestHandler[];
}>;
const guarded = (method: string, path: string): Table[number] => ({
  method,
  path,
  handlers: [authenticate, requireSessionAuth, scope, admin, handler],
});

const configTable = (): Table => [
  ...CONFIG_ROUTER_SPEC.routes.map((id) => {
    const [method, path] = id.split(' ');
    return guarded(method!.toLowerCase(), path!);
  }),
  {
    method: 'get',
    path: '/smtpConfig',
    handlers: [authenticate, scope, admin, handler],
  },
  {
    method: 'post',
    path: '/ai-models/identity/preview',
    handlers: [authenticate, scope, admin, handler],
  },
];

const userTable = (): Table => [
  guarded('patch', '/:id/email'),
  guarded('put', '/:id/unblock'),
  {
    method: 'put',
    path: '/:id',
    handlers: [authenticate, emailAndRole, scope, handler],
  },
  {
    method: 'post',
    path: '/',
    handlers: [authenticate, roleOnly, scope, handler],
  },
  {
    method: 'post',
    path: '/bulk/invite',
    handlers: [authenticate, roleOnly, scope, handler],
  },
  // Upload: the guard sits after the upload parser.
  {
    method: 'post',
    path: '/bulk/invite/upload',
    handlers: [
      authenticate,
      scope,
      function upload() {
        /* parser */
      },
      roleOnly,
      handler,
    ],
  },
  { method: 'get', path: '/:id', handlers: [authenticate, scope, handler] },
];

const build = (table: Table, extra?: (router: Router) => void): Router => {
  const router = Router();
  for (const t of table) (router as any)[t.method](t.path, ...t.handlers);
  extra?.(router);
  return router;
};

const find = (table: Table, method: string, path: string) =>
  table.find((t) => t.method === method && t.path === path)!;

interface Mutant {
  name: string;
  table?: (t: Table) => void;
  extra?: (r: Router) => void;
}

const configMutants: Mutant[] = [
  {
    name: 'a new POST under /authConfig, unguarded',
    table: (t) =>
      t.push({
        method: 'post',
        path: '/authConfig/okta',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'a new PUT under /authConfig',
    table: (t) =>
      t.push({
        method: 'put',
        path: '/authConfig/google',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'a new DELETE under /authConfig',
    table: (t) =>
      t.push({
        method: 'delete',
        path: '/authConfig/sso',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'a new router.all under /authConfig',
    table: (t) =>
      t.push({
        method: 'all',
        path: '/authConfig/x',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'guard dropped on google',
    table: (t) => {
      find(t, 'post', '/authConfig/google').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on frontendPublicUrl',
    table: (t) => {
      find(t, 'post', '/frontendPublicUrl').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on smtpConfig',
    table: (t) => {
      find(t, 'post', '/smtpConfig').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard moved after the scope check',
    table: (t) => {
      find(t, 'post', '/authConfig/microsoft').handlers = [
        authenticate,
        scope,
        requireSessionAuth,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard moved after the handler',
    table: (t) => {
      find(t, 'post', '/authConfig/microsoft').handlers = [
        authenticate,
        scope,
        admin,
        handler,
        requireSessionAuth,
      ];
    },
  },
  {
    name: 'a look-alike guard with the same name',
    table: (t) => {
      find(t, 'post', '/authConfig/google').handlers = [
        authenticate,
        noopGuard,
        scope,
        handler,
      ];
    },
  },
  {
    name: 'an expected route renamed away',
    table: (t) => {
      find(t, 'post', '/frontendPublicUrl').path = '/frontendUrl';
    },
  },
  {
    name: 'a new PUT /smtpConfig',
    table: (t) =>
      t.push({
        method: 'put',
        path: '/smtpConfig',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'a new DELETE /smtpConfig/enabled',
    table: (t) =>
      t.push({
        method: 'delete',
        path: '/smtpConfig/enabled',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'a variant path /frontendPublicUrl/v2',
    table: (t) =>
      t.push({
        method: 'post',
        path: '/frontendPublicUrl/v2',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'an unguarded duplicate registered first',
    table: (t) =>
      t.unshift({
        method: 'post',
        path: '/authConfig/google',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'guard dropped on an AI model provider write',
    table: (t) => {
      find(t, 'post', '/ai-models/providers').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'a new unguarded write under /ai-models',
    table: (t) =>
      t.push({
        method: 'post',
        path: '/ai-models/providers/import',
        handlers: [authenticate, scope, admin, handler],
      }),
  },
  {
    name: 'guard dropped on the Slack bot update',
    table: (t) => {
      find(t, 'put', '/slack-bot/:configId').handlers = [
        authenticate,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on platform settings',
    table: (t) => {
      find(t, 'post', '/platform/settings').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on the MCP access policy',
    table: (t) => {
      find(t, 'put', '/mcp/access').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on a web search provider write',
    table: (t) => {
      find(t, 'put', '/web-search/providers/:providerKey').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'an unguarded sub-router mounted at /authConfig',
    extra: (r) => {
      const sub = Router();
      sub.post('/okta', authenticate, scope, handler);
      r.use('/authConfig', sub);
    },
  },
  {
    name: 'an unguarded sub-router mounted at /authConfig/:provider',
    extra: (r) => {
      const sub = Router();
      sub.post('/callback', authenticate, scope, handler);
      r.use('/authConfig/:provider', sub);
    },
  },
  {
    name: 'an unguarded sub-router mounted with an array path',
    extra: (r) => {
      const sub = Router();
      sub.post('/okta', authenticate, scope, handler);
      r.use(['/authConfig'], sub);
    },
  },
];

const userMutants: Mutant[] = [
  {
    name: 'a new POST /:id/email-change, unguarded',
    table: (t) =>
      t.push({
        method: 'post',
        path: '/:id/email-change',
        handlers: [authenticate, scope, handler],
      }),
  },
  {
    name: 'a new PUT /:id/email/confirm, unguarded',
    table: (t) =>
      t.push({
        method: 'put',
        path: '/:id/email/confirm',
        handlers: [authenticate, scope, handler],
      }),
  },
  {
    name: 'guard dropped on PATCH /:id/email',
    table: (t) => {
      find(t, 'patch', '/:id/email').handlers = [
        authenticate,
        scope,
        admin,
        handler,
      ];
    },
  },
  {
    name: 'field guard dropped on PUT /:id',
    table: (t) => {
      find(t, 'put', '/:id').handlers = [authenticate, scope, handler];
    },
  },
  {
    name: 'field guard on PUT /:id without email',
    table: (t) => {
      find(t, 'put', '/:id').handlers = [
        authenticate,
        roleOnly,
        scope,
        handler,
      ];
    },
  },
  {
    name: 'field guard on PUT /:id after the handler',
    table: (t) => {
      find(t, 'put', '/:id').handlers = [
        authenticate,
        scope,
        handler,
        emailAndRole,
      ];
    },
  },
  {
    name: 'field guard dropped on the invite',
    table: (t) => {
      find(t, 'post', '/').handlers = [authenticate, scope, handler];
    },
  },
  {
    name: 'field guard dropped on the upload route',
    table: (t) => {
      find(t, 'post', '/bulk/invite/upload').handlers = [
        authenticate,
        scope,
        handler,
      ];
    },
  },
  {
    name: 'guard dropped on the unblock route',
    table: (t) => {
      find(t, 'put', '/:id/unblock').handlers = [authenticate, scope, handler];
    },
  },
];

describe('identity-control route scan: it fails on each way the table can go wrong', () => {
  it('passes the right config table and the right user table', () => {
    expect(
      identityControlViolations(
        routeChains(build(configTable())),
        CONFIG_ROUTER_SPEC,
        authenticate,
      ),
    ).to.deep.equal([]);
    expect(
      identityControlViolations(
        routeChains(build(userTable())),
        USER_ROUTER_SPEC,
        authenticate,
      ),
    ).to.deep.equal([]);
  });

  it('passes a router that puts the guard in router-level middleware', () => {
    const router = Router();
    router.use(authenticate, requireSessionAuth);
    router.post('/authConfig/google', scope, handler);
    const spec = {
      prefixes: ['/authConfig'],
      routes: ['POST /authConfig/google'],
      fieldRoutes: {},
    };
    expect(
      identityControlViolations(routeChains(router), spec, authenticate),
    ).to.deep.equal([]);
  });

  for (const m of configMutants) {
    it(`config router: reports ${m.name}`, () => {
      const table = configTable();
      m.table?.(table);
      const run = () =>
        identityControlViolations(
          routeChains(build(table, m.extra)),
          CONFIG_ROUTER_SPEC,
          authenticate,
        );
      // A mount the scan cannot read fails the test run itself, which is also a refusal.
      try {
        expect(run()).to.not.deep.equal([]);
      } catch (e) {
        expect(String(e)).to.contain('cannot read the mount path');
      }
    });
  }

  for (const m of userMutants) {
    it(`user router: reports ${m.name}`, () => {
      const table = userTable();
      m.table?.(table);
      expect(
        identityControlViolations(
          routeChains(build(table, m.extra)),
          USER_ROUTER_SPEC,
          authenticate,
        ),
      ).to.not.deep.equal([]);
    });
  }

  describe('a router that authenticates in router-level middleware', () => {
    const spec = {
      prefixes: [],
      routes: ['POST /'],
      fieldRoutes: {},
      routerLevelAuth: true,
    };
    const limiter = function limiter() {
      /* stand-in for a rate limiter */
    };
    const build2 = (own: RequestHandler[]) => {
      const router = Router();
      router.use(authenticate);
      router.use(limiter);
      router.use(admin);
      router.post('/', ...own);
      router.get('/', scope, handler);
      return router;
    };
    it("passes when the guard is the route's first handler", () => {
      expect(
        identityControlViolations(
          routeChains(build2([requireSessionAuth, scope, handler])),
          spec,
          authenticate,
        ),
      ).to.deep.equal([]);
    });
    for (const [name, own] of [
      ['guard dropped', [scope, handler]],
      ['guard after the scope check', [scope, requireSessionAuth, handler]],
      ['guard after the handler', [scope, handler, requireSessionAuth]],
      ['a look-alike guard', [noopGuard, scope, handler]],
    ] as Array<[string, RequestHandler[]]>) {
      it(`reports ${name}`, () => {
        expect(
          identityControlViolations(
            routeChains(build2(own)),
            spec,
            authenticate,
          ),
        ).to.not.deep.equal([]);
      });
    }
    it("still needs the guard straight after authenticate when authenticate is the route's own", () => {
      const router = Router();
      router.post('/', authenticate, scope, requireSessionAuth, handler);
      expect(
        identityControlViolations(routeChains(router), spec, authenticate),
      ).to.not.deep.equal([]);
    });
  });

  it('reports an unguarded POST that shares a route() with a guarded GET', () => {
    const router = Router();
    router
      .route('/authConfig/okta')
      .get(authenticate, requireSessionAuth, scope, handler)
      .post(authenticate, scope, handler);
    const spec = { prefixes: ['/authConfig'], routes: [], fieldRoutes: {} };
    expect(
      identityControlViolations(routeChains(router), spec, authenticate),
    ).to.deep.equal([
      'POST /authConfig/okta: requireSessionAuth does not directly follow authenticate',
    ]);
  });

  it('matches a prefix whatever its case, but not a longer sibling', () => {
    const router = Router();
    router.post('/AuthConfig/okta', authenticate, scope, handler);
    router.post('/authConfigBackup', authenticate, scope, handler);
    const spec = { prefixes: ['/authConfig'], routes: [], fieldRoutes: {} };
    expect(
      identityControlViolations(routeChains(router), spec, authenticate),
    ).to.deep.equal([
      'POST /AuthConfig/okta: requireSessionAuth does not directly follow authenticate',
    ]);
  });

  it('reads the path of a sub-router mounted on a parameter', () => {
    const router = Router();
    const sub = Router();
    sub.post('/callback', authenticate, scope, handler);
    router.use('/authConfig/:provider', sub);
    expect(routeChains(router).map((c) => c.id)).to.deep.equal([
      'POST /authConfig/:provider/callback',
    ]);
  });
});

describe('config:write routes: a token is refused before the scope check', () => {
  const configWrite = requireScopes(OAuthScopeNames.CONFIG_WRITE);
  const configRead = requireScopes(OAuthScopeNames.CONFIG_READ);
  const spec = { prefixes: [], routes: [], fieldRoutes: {} };
  const scan = (...routes: Array<[string, string, RequestHandler[]]>) => {
    const router = Router();
    for (const [method, path, handlers] of routes)
      (router as any)[method](path, ...handlers);
    return identityControlViolations(routeChains(router), spec, authenticate);
  };

  it('passes a guarded config:write route and leaves config:read routes alone', () => {
    expect(
      scan(
        [
          'put',
          '/anything',
          [authenticate, requireSessionAuth, configWrite, admin, handler],
        ],
        ['get', '/anything', [authenticate, configRead, admin, handler]],
      ),
    ).to.deep.equal([]);
  });

  it('reports a new config:write route with no session guard', () => {
    expect(
      scan([
        'post',
        '/new-setting',
        [authenticate, configWrite, admin, handler],
      ]),
    ).to.not.deep.equal([]);
  });

  it('reports the guard placed after the config:write scope check', () => {
    expect(
      scan([
        'post',
        '/new-setting',
        [authenticate, configWrite, requireSessionAuth, admin, handler],
      ]),
    ).to.not.deep.equal([]);
  });

  it('reports a look-alike guard', () => {
    expect(
      scan([
        'post',
        '/new-setting',
        [authenticate, noopGuard, configWrite, admin, handler],
      ]),
    ).to.not.deep.equal([]);
  });

  it('reports the guard placed before authenticate', () => {
    expect(
      scan([
        'post',
        '/new-setting',
        [requireSessionAuth, authenticate, configWrite, admin, handler],
      ]),
    ).to.deep.equal([
      'POST /new-setting: requireSessionAuth runs before authenticate',
    ]);
  });

  it('accepts authenticate from router-level middleware', () => {
    const router = Router();
    router.use(authenticate);
    router.post('/new-setting', requireSessionAuth, configWrite, admin, handler);
    expect(
      identityControlViolations(routeChains(router), spec, authenticate),
    ).to.deep.equal([]);
  });

  it('requireScopes says which scopes it checks', () => {
    expect(
      (configWrite as unknown as { requiredScopes: string[] }).requiredScopes,
    ).to.deep.equal(['config:write']);
  });
});

describe('the role rule used by the field guard', () => {
  it('normalises like the controllers and refuses odd shapes', () => {
    for (const v of [
      'admin',
      ' admin',
      'ADMIN',
      'Admin\t',
      ['member', ' Admin '],
      { $eq: 'admin' },
      1,
      true,
    ]) {
      expect(isAdminRole(v), JSON.stringify(v)).to.equal(true);
    }
    for (const v of [
      'member',
      'Member ',
      '',
      null,
      undefined,
      [],
      ['member'],
    ]) {
      expect(isAdminRole(v), JSON.stringify(v)).to.equal(false);
    }
  });

  it('refuses a token on any admin spelling and lets other values through', () => {
    const guard = requireSessionAuthForFields({ role: isAdminRole });
    const run = (body: unknown, isOAuth: boolean) => {
      let result: unknown = 'not called';
      guard(
        { user: { isOAuth }, body } as never,
        {} as never,
        (e?: unknown) => {
          result = e ?? null;
        },
      );
      return result as { statusCode?: number } | null;
    };
    expect(run({ role: ' Admin ' }, true)?.statusCode).to.equal(403);
    expect(run({ role: ['admin'] }, true)?.statusCode).to.equal(403);
    expect(run({ role: 'member' }, true)).to.equal(null);
    expect(run({ fullName: 'x' }, true)).to.equal(null);
    expect(run({ role: ' Admin ' }, false)).to.equal(null);
  });
});

describe('allowlist routes: the scan compares the whole allowlist', () => {
  const spec: IdentityControlSpec = {
    prefixes: [],
    routes: [],
    fieldRoutes: {},
    allowlistRoutes: {
      'POST /': {
        allowed: { name: true, config: ['sync', 'filters'] },
        emptyAllowed: ['config.auth'],
      },
    },
  };
  const build = (guard: unknown) => {
    const router = Router();
    router.post('/', authenticate, guard as never, scope, handler);
    return router;
  };
  const scan = (guard: unknown) =>
    identityControlViolations(routeChains(build(guard)), spec, authenticate);

  it('passes the right guard', () => {
    expect(
      scan(
        requireSessionAuthExcept(
          { name: true, config: ['sync', 'filters'] },
          { emptyAllowed: ['config.auth'] },
        ),
      ),
    ).to.deep.equal([]);
  });

  for (const [name, guard] of [
    [
      'a nested list loosened to true',
      requireSessionAuthExcept(
        { name: true, config: true },
        { emptyAllowed: ['config.auth'] },
      ),
    ],
    [
      'a nested list widened',
      requireSessionAuthExcept(
        { name: true, config: ['sync', 'filters', 'auth'] },
        { emptyAllowed: ['config.auth'] },
      ),
    ],
    [
      'a top-level key added',
      requireSessionAuthExcept(
        { name: true, other: true, config: ['sync', 'filters'] },
        { emptyAllowed: ['config.auth'] },
      ),
    ],
    [
      'an empty-allowed key added',
      requireSessionAuthExcept(
        { name: true, config: ['sync', 'filters'] },
        { emptyAllowed: ['config.auth', 'baseUrl'] },
      ),
    ],
    [
      'the empty-allowed list dropped',
      requireSessionAuthExcept({ name: true, config: ['sync', 'filters'] }),
    ],
    [
      'no guard',
      function noGuard() {
        /* nothing */
      },
    ],
  ] as Array<[string, unknown]>) {
    it(`reports ${name}`, () => {
      expect(scan(guard)).to.not.deep.equal([]);
    });
  }
});
