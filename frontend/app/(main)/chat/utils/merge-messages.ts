import type { ThreadMessageLike } from '@assistant-ui/react';

type Custom = Record<string, unknown>;

function customOf(m: ThreadMessageLike): Custom | undefined {
  return m.metadata?.custom as Custom | undefined;
}

function numberField(m: ThreadMessageLike, key: string): number | undefined {
  const v = customOf(m)?.[key];
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

/** Position in the conversation; absent on optimistic rows and on chats that predate collaboration. */
export function seqOf(m: ThreadMessageLike): number | undefined {
  return numberField(m, 'seq');
}

/** The feed `rev` a row arrived with. A row with a higher `rev` replaces one with a lower. */
export function revOf(m: ThreadMessageLike): number | undefined {
  return numberField(m, 'rev');
}

export function clientMessageIdOf(m: ThreadMessageLike): string | undefined {
  const v = customOf(m)?.clientMessageId;
  return typeof v === 'string' && v ? v : undefined;
}

/** Highest `seq` in the list, or -1 when no row has one. */
export function maxSeq(messages: readonly ThreadMessageLike[]): number {
  let max = -1;
  for (const m of messages) {
    const s = seqOf(m);
    if (s !== undefined && s > max) max = s;
  }
  return max;
}

function shouldReplace(current: ThreadMessageLike, incoming: ThreadMessageLike): boolean {
  // The stored row for an optimistic one always wins: it carries the server's seq and id.
  if (seqOf(current) === undefined && seqOf(incoming) !== undefined) return true;
  const curRev = revOf(current);
  const incRev = revOf(incoming);
  if (curRev === undefined) return incRev !== undefined || JSON.stringify(current) !== JSON.stringify(incoming);
  if (incRev === undefined) return false;
  return incRev > curRev;
}

/** Index at which a row with `seq` goes: after the last row with a lower or equal seq, never past the pending tail. */
function insertionIndex(rows: readonly ThreadMessageLike[], seq: number): number {
  let tailStart = rows.length;
  while (tailStart > 0 && seqOf(rows[tailStart - 1]) === undefined && isPending(rows[tailStart - 1])) {
    tailStart -= 1;
  }
  let after = -1;
  for (let i = 0; i < tailStart; i += 1) {
    const s = seqOf(rows[i]);
    if (s !== undefined && s <= seq) after = i;
  }
  return after >= 0 ? after + 1 : tailStart;
}

/** A row the client made up and the server has not stored yet: an optimistic user row or a streaming placeholder. */
function isPending(m: ThreadMessageLike): boolean {
  return clientMessageIdOf(m) !== undefined || customOf(m)?.pending === true;
}

/**
 * Merges stored rows into the slot's list. Rows are matched by `id`, or by `clientMessageId` for the
 * sender's own optimistic row. Idempotent: merging the same rows again returns `existing` itself, so the
 * caller can skip a store write.
 */
export function mergeMessagesById(
  existing: readonly ThreadMessageLike[],
  incoming: readonly ThreadMessageLike[],
): ThreadMessageLike[] {
  let result = existing as ThreadMessageLike[];
  let changed = false;
  const ensureCopy = () => {
    if (!changed) result = [...existing];
    changed = true;
  };

  const latest = new Map<string, ThreadMessageLike>();
  const unkeyed: ThreadMessageLike[] = [];
  for (const row of incoming) {
    const key = row.id ?? (clientMessageIdOf(row) ? `cid:${clientMessageIdOf(row)}` : undefined);
    if (key === undefined) unkeyed.push(row);
    else latest.set(key, row);
  }

  for (const row of [...latest.values(), ...unkeyed]) {
    const cid = clientMessageIdOf(row);
    let idx = row.id !== undefined ? result.findIndex((m) => m.id === row.id) : -1;
    if (idx < 0 && cid) idx = result.findIndex((m) => clientMessageIdOf(m) === cid && m.role === row.role);

    if (idx >= 0) {
      if (!shouldReplace(result[idx], row)) continue;
      ensureCopy();
      const seq = seqOf(row);
      if (seq === undefined) {
        result[idx] = row;
      } else {
        result.splice(idx, 1);
        result.splice(insertionIndex(result, seq), 0, row);
      }
      continue;
    }

    ensureCopy();
    const seq = seqOf(row);
    if (seq === undefined) result.push(row);
    else result.splice(insertionIndex(result, seq), 0, row);
  }
  return result;
}
