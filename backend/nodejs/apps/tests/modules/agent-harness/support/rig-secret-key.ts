import { randomBytes } from 'node:crypto';

/**
 * A rig that composes the harness signs sandbox bindings as the broker does, with SECRET_KEY: the gateway-token route
 * refuses a proof made without one. A process that runs without a key (the itest runner, CI) gets a generated one for
 * the rig's life; the returned function puts the environment back.
 */
export function ensureRigSecretKey(): () => void {
  if (process.env.SECRET_KEY) return () => undefined;
  process.env.SECRET_KEY = randomBytes(32).toString('hex');
  return () => {
    delete process.env.SECRET_KEY;
  };
}
