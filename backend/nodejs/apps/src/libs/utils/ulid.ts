import { randomBytes } from 'crypto';

const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/**
 * A ULID (https://github.com/ulid/spec): 48 bits of milliseconds then 80
 * random bits, as 26 Crockford base-32 characters, so ids sort by creation.
 */
export function ulid(
  now: number = Date.now(),
  random: (n: number) => Buffer = randomBytes,
): string {
  let time = '';
  let ms = now;
  for (let i = 0; i < 10; i += 1) {
    time = CROCKFORD[ms % 32] + time;
    ms = Math.floor(ms / 32);
  }
  // 80 random bits are 16 characters of 5 bits: read them 5 at a time from the byte stream.
  const bytes = random(10);
  let tail = '';
  for (let i = 0; i < 16; i += 1) {
    const bit = i * 5;
    const pair = ((bytes[bit >> 3] ?? 0) << 8) | (bytes[(bit >> 3) + 1] ?? 0);
    tail += CROCKFORD[(pair >> (11 - (bit & 7))) & 31];
  }
  return time + tail;
}
