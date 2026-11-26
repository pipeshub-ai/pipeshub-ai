import { createHash } from 'node:crypto';
import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import { componentDocumentPath } from '../configuration/encrypted-config-document-store';
import type { ComponentStoreLogins } from '../../application/component-credential-publisher';
import { componentRedisLogin } from '../../domain/component-keys';
import type { SandboxPlaneComponent } from '../../domain/credential-roles';

const READ_COMMANDS = [
  '+get',
  '+mget',
  '+exists',
  '+ttl',
  '+pttl',
  '+scan',
  '+ping',
  '+hello',
  '+auth',
  '+select',
  '+client|setname',
  '+subscribe',
  '+unsubscribe',
  '+psubscribe',
  '+punsubscribe',
  '+quit',
  '+reset',
];
// The proxy's durable state (bindings, grants, stops, the TLS authority): written with a compare-and-set script.
const EGRESS_STATE_COMMANDS = ['+set', '+del', '+eval', '+evalsha'];

/**
 * A store that refuses `ACL SETUSER` (some managed Redis offerings) and on which the component's user does not log
 * in either: its operator must create the user (R38IDENT-FU-12). The message says how; it names the user, never
 * the password.
 */
export class ComponentLoginMissing extends Error {
  override readonly name = 'ComponentLoginMissing';
  constructor(
    readonly component: SandboxPlaneComponent,
    readonly username: string,
  ) {
    super(
      `The Redis store refused to create the ${component} login and it does not exist yet. Create it on the store ` +
        `with the commands \`node dist/scripts/component-acl.js\` prints in the app container, then restart nothing: Node ` +
        `retries every five minutes.`,
    );
  }
}

/** The full `ACL SETUSER` command for a component: the password only as its SHA-256, as Redis stores it. */
export function componentAclCommand(
  component: SandboxPlaneComponent,
  key: string,
  namespace: string,
  kvPrefix: string,
): string[] {
  const { username, password } = componentRedisLogin(component, key);
  return [
    'ACL',
    'SETUSER',
    username,
    'reset',
    'on',
    `#${createHash('sha256').update(password, 'utf8').digest('hex')}`,
    ...componentAclRules(component, namespace, kvPrefix),
  ];
}

/**
 * ADR-KEY-02 D3 (SEC-D-03): a Redis ACL user per sandbox-plane component, whose password both sides derive from the
 * component's key. It reads its own document (which carries the endpoints it calls), writes only the proxy's egress state, subscribes to
 * the configuration-change channel, and nothing else: every configuration secret is NOPERM, so a foothold in the
 * proxy cannot dump the KV store even before it would have to decrypt it. Applied idempotently on every publish.
 *
 * A store that refuses `ACL SETUSER` (R38IDENT-FU-12) is accepted when the component's own login already works
 * there: its operator created the user. Otherwise `ComponentLoginMissing` says what the operator must do.
 */
export class RedisComponentLogins implements ComponentStoreLogins {
  constructor(
    private readonly provider: IRedisConnectionProvider,
    private readonly key: (component: SandboxPlaneComponent) => string | null,
    private readonly kvPrefix: string,
  ) {}

  async apply(component: SandboxPlaneComponent): Promise<void> {
    const key = this.key(component);
    if (key === null) return;
    const [command, ...args] = componentAclCommand(
      component,
      key,
      this.provider.keyNamespace,
      this.kvPrefix,
    ) as [string, ...string[]];
    try {
      await this.provider.getClient().call(command, ...args);
      return;
    } catch (refused) {
      const { username, password } = componentRedisLogin(component, key);
      if (await this.logsIn(username, password)) return;
      throw Object.assign(new ComponentLoginMissing(component, username), {
        cause: refused,
      });
    }
  }

  /** The component's own login, on a connection of its own: the shared client's identity never changes. */
  private async logsIn(username: string, password: string): Promise<boolean> {
    const client = this.provider.createClient({
      connectTimeoutMs: 5_000,
      maxRetriesPerRequest: 1,
    });
    try {
      return (await client.call('AUTH', username, password)) === 'OK';
    } catch {
      return false;
    } finally {
      client.disconnect();
    }
  }
}

/** The user's key, channel and command rules (without the password), for the lane test and the ADR's table. */
export function componentAclRules(
  component: SandboxPlaneComponent,
  namespace: string,
  kvPrefix: string,
): string[] {
  const scoped = namespace ? `${namespace}:` : '';
  const kv = `${scoped}${kvPrefix}`;
  // Not the plain endpoints document: the component reads its peers' addresses from its sealed one (R38IDENT-FU-10).
  const keys = [
    `~${kv}${componentDocumentPath(component)}`,
    ...(component === 'egressProxy' ? [`~${namespace}harness:egress:*`] : []),
  ];
  return [
    ...keys,
    `&${scoped}pipeshub:cache:invalidate`,
    '-@all',
    ...READ_COMMANDS,
    ...(component === 'egressProxy' ? EGRESS_STATE_COMMANDS : []),
  ];
}
