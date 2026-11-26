import { randomUUID } from 'crypto';
import type { Redis } from 'ioredis';
import type { IRedisConnectionProvider } from '../../../../libs/services/redis/connectionProvider.interface';
import type {
  EngineSignInRelay,
  SignInRelayAnswer,
  SignInRelayRequest,
  SignInRevocation,
} from '../../ports/engine-signins';

/** The worker's channels (`app/harness/engines/signin_relay.py`). */
export const ATTEMPT_PREFIX = 'harness:signin:attempt:';
export const REPLY_PREFIX = 'harness:signin:reply:';
export const REVOKE_CHANNEL = 'harness:signin:revoke';
/** When an org, a person or a conversation was last revoked (epoch ms), read by a turn on a kept sandbox. */
export const REVOKED_PREFIX = 'harness:signin:revoked:';
/** The longest a sign-in can be kept (`KEEP_MAX_BOUNDS_S` in `harness_core/engines/signin.py`). */
export const REVOCATION_MEMORY_S = 24 * 60 * 60;

/** The scope a revocation names, as the worker's `revocation_scopes` reads it. */
export const revocationScope = (revocation: SignInRevocation): string =>
  revocation.userId !== undefined
    ? `${revocation.orgId}:u:${revocation.userId}`
    : revocation.sessionId !== undefined
      ? `${revocation.orgId}:s:${revocation.sessionId}`
      : revocation.orgId;

export const signinChannel = (name: string, namespace = ''): string =>
  namespace ? `${namespace}:${name}` : name;

/**
 * SIGNIN's relay on Redis pub/sub: a request on the attempt's channel, the
 * answer on a reply channel only this process listens on. Pub/sub keeps no
 * message, so the link, the code and the answer are never stored.
 */
export class RedisEngineSignInRelay implements EngineSignInRelay {
  private subscriber: Redis | null = null;
  private readonly waiting = new Map<
    string,
    (answer: SignInRelayAnswer) => void
  >();

  constructor(private readonly provider: IRedisConnectionProvider) {}

  async ask(
    attemptId: string,
    caller: { orgId: string; userId: string },
    request: SignInRelayRequest,
    timeoutMs: number,
  ): Promise<SignInRelayAnswer | null> {
    const ns = this.provider.keyNamespace;
    const reply = signinChannel(`${REPLY_PREFIX}${randomUUID()}`, ns);
    const subscriber = this.listener();
    const answered = new Promise<SignInRelayAnswer | null>((resolve) => {
      const timer = setTimeout(() => {
        this.waiting.delete(reply);
        resolve(null);
      }, timeoutMs);
      timer.unref();
      this.waiting.set(reply, (answer) => {
        clearTimeout(timer);
        this.waiting.delete(reply);
        resolve(answer);
      });
    });
    try {
      await subscriber.subscribe(reply);
      await this.provider.getClient().publish(
        signinChannel(`${ATTEMPT_PREFIX}${attemptId}`, ns),
        JSON.stringify({
          ...request,
          actorId: caller.userId,
          orgId: caller.orgId,
          reply,
        }),
      );
      return await answered;
    } finally {
      this.waiting.delete(reply);
      void subscriber.unsubscribe(reply).catch(() => undefined);
    }
  }

  /**
   * Remembered first, then announced: a worker that hears the message ends
   * the sign-ins it holds now, and a turn that later starts on a sandbox kept
   * between turns (whose worker may have missed the message) finds the time.
   */
  async revoke(revocation: SignInRevocation): Promise<void> {
    const ns = this.provider.keyNamespace;
    const client = this.provider.getClient();
    await client.set(
      signinChannel(`${REVOKED_PREFIX}${revocationScope(revocation)}`, ns),
      String(Date.now()),
      'EX',
      REVOCATION_MEMORY_S,
    );
    await client.publish(
      signinChannel(REVOKE_CHANNEL, ns),
      JSON.stringify(revocation),
    );
  }

  private listener(): Redis {
    if (this.subscriber !== null) return this.subscriber;
    const subscriber = this.provider.createPubSubClient();
    subscriber.on('message', (channel: string, message: string) => {
      const done = this.waiting.get(channel);
      if (done === undefined) return;
      try {
        const parsed: unknown = JSON.parse(message);
        if (parsed !== null && typeof parsed === 'object' && 'ok' in parsed) {
          done(parsed as SignInRelayAnswer);
        }
      } catch {
        // A malformed answer is no answer: the request times out.
      }
    });
    subscriber.on('error', () => undefined);
    this.subscriber = subscriber;
    return subscriber;
  }
}
