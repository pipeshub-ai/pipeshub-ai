import { UowUsageError } from './errors';

/**
 * Advisory-lock keys are two integers: a registered class and an object id. One shared constant would let any
 * component block every other. Only transaction-scoped forms exist (a session lock would outlive the
 * transaction on a pooled connection).
 */
export interface LockKey {
  readonly name: string;
  readonly classId: number;
}

const byName = new Map<string, LockKey>();
const byClass = new Map<number, LockKey>();

/** Classes the database kernel uses itself (its fences and the per-email lock). No component can register them, under any name. */
const KERNEL_CLASSES: ReadonlyMap<number, string> = new Map([
  [7340, 'docstore_fence'],
  [7341, 'profile_fence'],
  [7350, 'person_email'],
]);

export function registerLockKey(name: string, classId: number): LockKey {
  if (
    KERNEL_CLASSES.has(classId) ||
    [...KERNEL_CLASSES.values()].includes(name)
  ) {
    throw new UowUsageError(
      `lock key '${name}' (class ${String(classId)}): the kernel reserves its own lock classes`,
    );
  }
  if (!Number.isInteger(classId) || classId < 1 || classId > 2 ** 31 - 1) {
    throw new UowUsageError(
      `lock key '${name}': classId must be a positive 32-bit integer`,
    );
  }
  const taken = byClass.get(classId);
  if (taken && taken.name !== name) {
    throw new UowUsageError(
      `lock class ${classId} is already registered as '${taken.name}'`,
    );
  }
  const existing = byName.get(name);
  if (existing && existing.classId !== classId) {
    throw new UowUsageError(
      `lock key '${name}' is already registered with class ${existing.classId}`,
    );
  }
  const key = existing ?? Object.freeze({ name, classId });
  byName.set(name, key);
  byClass.set(classId, key);
  return key;
}

export function lockKeyArgs(key: LockKey, objectId: number): [number, number] {
  if (KERNEL_CLASSES.has(key.classId))
    throw new UowUsageError('lock key is in a class the kernel reserves');
  if (byName.get(key.name) !== key)
    throw new UowUsageError(`lock key '${key.name}' is not registered`);
  if (!Number.isInteger(objectId) || Math.abs(objectId) > 2 ** 31 - 1) {
    throw new UowUsageError('lock object id must be a 32-bit integer');
  }
  return [key.classId, objectId];
}
