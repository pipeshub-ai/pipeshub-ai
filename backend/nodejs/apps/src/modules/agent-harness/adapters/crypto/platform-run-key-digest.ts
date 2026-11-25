import { randomBytes } from 'node:crypto';
import { HmacRunKeyDigest } from '../../domain/run-key-digest';
import { getHashedSecretKeyRing } from '../../../configuration_manager/config/config';

/**
 * Every replica of the deployment derives the same one, from its SECRET_KEY, read on first use; a digest made
 * under the ring's other key (SECRET_KEY_PREVIOUS or SECRET_KEY_NEXT) still matches.
 */
export const platformRunKeyDigest = (): HmacRunKeyDigest =>
  new HmacRunKeyDigest(
    () => Buffer.from(getHashedSecretKeyRing()[0] as string, 'hex'),
    () =>
      getHashedSecretKeyRing()
        .slice(1)
        .map((hex) => Buffer.from(hex, 'hex')),
  );

/** For a store that lives and dies with its process: no stored key outlives the secret. */
export const ephemeralRunKeyDigest = (): HmacRunKeyDigest =>
  new HmacRunKeyDigest(() => randomBytes(32));
