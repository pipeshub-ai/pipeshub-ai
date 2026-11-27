/**
 * Governed memory (P7.3): a person's own memories. Views carry no label sources, only
 * whether the label is complete.
 */
import { apiClient } from '@/lib/api';
import { harnessCall } from '../harness-ui/refusal';
import { isObject, listOf, num, oneOf, QUIET, text } from '../harness-ui/pick';
import type { ErrorCodeOf } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';

export const MEMORY_SCOPES = ['user', 'session', 'agent'] as const;
export const MEMORY_KINDS = ['fact', 'preference'] as const;
export const MEMORY_STATUSES = ['pending', 'proposed', 'accepted'] as const;

export type MemoryStatus = (typeof MEMORY_STATUSES)[number];
export type ReviewDecision = 'accepted' | 'rejected';

export interface Memory {
  memoryId: string;
  scope: (typeof MEMORY_SCOPES)[number];
  kind: (typeof MEMORY_KINDS)[number];
  content: string;
  status: MemoryStatus;
  origin: 'model' | 'user';
  agentKey: string | null;
  /** False: it came from content of unknown origin; only its owner sees it, and it is never recalled until accepted. */
  labelComplete: boolean;
  /** Content someone outside the organization could write (a web page, an outside email) led to it (SEC-B-05). */
  followedOutsideContent: boolean;
  createdAt: number;
}

/** `review` = what waits for a decision; `pending` and `proposed` are both that. */
export type MemoryFilter = 'all' | 'review' | 'accepted';

export function toMemory(value: unknown): Memory | null {
  if (!isObject(value)) return null;
  const memoryId = text(value.memoryId);
  const scope = oneOf(value.scope, MEMORY_SCOPES);
  const kind = oneOf(value.kind, MEMORY_KINDS);
  const status = oneOf(value.status, MEMORY_STATUSES);
  const content = typeof value.content === 'string' ? value.content : null;
  if (!memoryId || !scope || !kind || !status || content === null) return null;
  return {
    memoryId,
    scope,
    kind,
    content,
    status,
    origin: value.origin === 'user' ? 'user' : 'model',
    agentKey: text(value.agentKey),
    labelComplete: value.labelComplete === true,
    followedOutsideContent: value.followedOutsideContent === true,
    createdAt: num(value.createdAt) ?? 0,
  };
}

const OWN = '/api/v1/agent-harness/memories';
const PREFERENCE = `${OWN}/preference`;
const seg = encodeURIComponent;

/** On unless the server says the person turned it off. */
const rememberOf = (data: unknown): boolean => !(isObject(data) && data.remember === false);

const memoriesOf = (data: unknown) => listOf(isObject(data) ? data.memories : null, toMemory);
const memoryOf = (data: unknown): Memory | null => {
  if (!isObject(data)) return null;
  return toMemory(isObject(data.memory) ? data.memory : data);
};

async function listOwnByStatus(status: MemoryStatus | null): Promise<Memory[]> {
  const { data } = await apiClient.get(OWN, { ...QUIET, ...(status ? { params: { status } } : {}) });
  return memoriesOf(data);
}

export const MemoriesApi = {
  /** The route filters by one status; "waiting for review" is two of them. */
  listOwn: (filter: MemoryFilter) =>
    harnessCall(async () => {
      if (filter === 'accepted') return listOwnByStatus('accepted');
      if (filter === 'all') return listOwnByStatus(null);
      const [pending, proposed] = await Promise.all([listOwnByStatus('pending'), listOwnByStatus('proposed')]);
      return [...proposed, ...pending].sort((a, b) => b.createdAt - a.createdAt);
    }),

  edit: (memoryId: string, content: string) =>
    harnessCall(async () => memoryOf((await apiClient.patch(`${OWN}/${seg(memoryId)}`, { content }, QUIET)).data), true),

  remove: (memoryId: string) =>
    harnessCall(async () => {
      await apiClient.delete(`${OWN}/${seg(memoryId)}`, QUIET);
    }, true),

  /** The person's own switch: whether their runs use and keep memories. */
  remember: () => harnessCall(async () => rememberOf((await apiClient.get(PREFERENCE, QUIET)).data)),

  setRemember: (remember: boolean) =>
    harnessCall(async () => rememberOf((await apiClient.put(PREFERENCE, { remember }, QUIET)).data), true),

  review: (memoryId: string, decision: ReviewDecision) =>
    harnessCall(async () => {
      await apiClient.post(`${OWN}/${seg(memoryId)}/review`, { decision }, QUIET);
    }, true),
};

/** P7.3's refusals: the catalogue's `memory` group, every one. */
export const MEMORY_REFUSALS: Readonly<Record<ErrorCodeOf<'memory'>, string>> = {
  MEMORY_NOT_FOUND: 'memories.refusal.notFound',
  MEMORY_INVALID: 'memories.refusal.invalid',
  MEMORY_WRITE_NOT_GRANTED: 'memories.refusal.writeNotGranted',
  MEMORY_LIMIT: 'memories.refusal.limit',
  MEMORY_REPLACE_UNKNOWN: 'memories.refusal.replaceUnknown',
  MEMORY_NOT_REVIEWABLE: 'memories.refusal.notReviewable',
  MEMORY_POLICY_INVALID: 'memories.refusal.policyInvalid',
};
