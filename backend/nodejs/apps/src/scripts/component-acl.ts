// Prints the Redis ACL users the egress proxy and the sandbox broker log in as (ADR-KEY-02 D3), for a managed Redis
// that refuses `ACL SETUSER` from Node (R38IDENT-FU-12). Run it where the backend runs, with its environment:
//   node dist/scripts/component-acl.js             one `ACL SETUSER` line per component, the password as its SHA-256
//   node dist/scripts/component-acl.js --password  also each user's name and password, for a console that asks for them
// Node retries the publish every five minutes; once the users exist the components log in on their own.
import { componentAclCommand } from '../modules/agent-harness/adapters/redis/redis-component-logins';
import {
  componentKey,
  componentRedisLogin,
} from '../modules/agent-harness/domain/component-keys';
import {
  SANDBOX_PLANE_COMPONENTS,
  SandboxPlaneComponent,
} from '../modules/agent-harness/domain/credential-roles';

type Env = Record<string, string | undefined>;

const quoted = (arg: string): string =>
  /^[\w#~&+:/*.@-]+$/.test(arg)
    ? arg
    : `"${arg.replace(/(["\\$`])/g, '\\$1')}"`;

/** What the operator runs, one entry per component this install holds a key for. */
export function componentAclLines(env: Env, withPassword: boolean): string[] {
  const lines: string[] = [];
  for (const component of Object.keys(
    SANDBOX_PLANE_COMPONENTS,
  ) as SandboxPlaneComponent[]) {
    const key = componentKey(component, env);
    if (key === null) continue;
    const command = componentAclCommand(
      component,
      key,
      env.REDIS_KEY_NAMESPACE ?? '',
      env.REDIS_KV_PREFIX || 'pipeshub:kv:',
    );
    lines.push(`# ${component}`, command.map(quoted).join(' '));
    if (withPassword) {
      const { username, password } = componentRedisLogin(component, key);
      lines.push(`#   user ${username}`, `#   password ${password}`);
    }
  }
  return lines;
}

if (require.main === module) {
  const lines = componentAclLines(
    process.env,
    process.argv.includes('--password'),
  );
  if (lines.length === 0) {
    process.stderr.write(
      "No component key is set (EGRESS_PROXY_COMPONENT_KEY, SANDBOX_BROKER_COMPONENT_KEY): run this with the app container's environment.\n",
    );
    process.exitCode = 1;
  } else {
    process.stdout.write(`${lines.join('\n')}\n`);
  }
}
