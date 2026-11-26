import { randomBytes } from 'crypto';
export { isValidId } from './mongo';

/** An id as a string, whatever the caller holds (a string, or a driver id from older code). */
export const idOf = (id: unknown): string => String(id);

/** Whether the value is the driver's own id object rather than a string. */
export const isNativeId = (v: unknown): boolean =>
  typeof v === 'object' &&
  v !== null &&
  (v as { _bsontype?: unknown })._bsontype === 'ObjectId';

let counter = randomBytes(3).readUIntBE(0, 3);
const processBytes = randomBytes(5).toString('hex');

/** A new ObjectId-shaped id (4 bytes of seconds, 5 random per process, 3 counter), as 24 lower-case hex digits. */
export function newObjectId(): string {
  counter = (counter + 1) & 0xffffff;
  const seconds = Math.floor(Date.now() / 1000)
    .toString(16)
    .padStart(8, '0');
  return seconds + processBytes + counter.toString(16).padStart(6, '0');
}
