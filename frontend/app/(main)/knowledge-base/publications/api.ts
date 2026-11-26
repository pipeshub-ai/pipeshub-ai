/**
 * Knowledge publications (P2.13): an owner of a collection, or an org admin,
 * discloses it to an audience for answers only or in full, until an expiry.
 * Behind Labs `ENABLE_KNOWLEDGE_PUBLICATIONS`; the query service decides who
 * may, from the caller's own credential.
 */
import type { ErrorCodeOf } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';
import { apiClient } from '@/lib/api';
import { harnessCall } from '@/workspace/harness-ui/refusal';
import { isObject, listOf, num, oneOf, QUIET, strings, text } from '@/workspace/harness-ui/pick';

export const PUBLICATION_MODES = ['answer_only', 'full'] as const;
export type PublicationMode = (typeof PUBLICATION_MODES)[number];
export type ContainerKind = 'kb' | 'recordGroup';

export interface Audience {
  userIds: string[];
  teamIds: string[];
  org: boolean;
}

export interface Publication {
  id: string;
  containerKind: ContainerKind;
  containerId: string;
  audience: Audience;
  mode: PublicationMode;
  createdBy: string;
  createdAt: number;
  expiresAt: number | null;
  revokedAt: number | null;
}

export interface NewPublication {
  containerKind: ContainerKind;
  containerId: string;
  audience: Audience;
  mode: PublicationMode;
  expiresAt: number | null;
}

const KINDS: readonly ContainerKind[] = ['kb', 'recordGroup'];

const toAudience = (value: unknown): Audience => ({
  userIds: isObject(value) ? strings(value.userIds) : [],
  teamIds: isObject(value) ? strings(value.teamIds) : [],
  org: isObject(value) && value.org === true,
});

export function toPublication(value: unknown): Publication | null {
  if (!isObject(value)) return null;
  const id = text(value.id);
  const containerKind = oneOf(value.containerKind, KINDS);
  const containerId = text(value.containerId);
  const mode = oneOf(value.mode, PUBLICATION_MODES);
  if (!id || !containerKind || !containerId || !mode) return null;
  return {
    id,
    containerKind,
    containerId,
    audience: toAudience(value.audience),
    mode,
    createdBy: text(value.createdBy) ?? '',
    createdAt: num(value.createdAt) ?? 0,
    expiresAt: num(value.expiresAt),
    revokedAt: num(value.revokedAt),
  };
}

const BASE = '/api/v1/agent-harness/publications';

const one = (data: unknown): Publication => {
  const publication = toPublication(data);
  if (publication === null) throw new Error('Unexpected publication');
  return publication;
};

export const PublicationsApi = {
  list: (containerKind: ContainerKind, containerId: string) =>
    harnessCall(async () => {
      const { data } = await apiClient.get(BASE, { ...QUIET, params: { containerKind, containerId } });
      return listOf(isObject(data) ? data.publications : null, toPublication);
    }),

  create: (input: NewPublication) =>
    harnessCall(async () => {
      const body = {
        containerKind: input.containerKind,
        containerId: input.containerId,
        audience: { userIds: input.audience.userIds, teamIds: input.audience.teamIds, org: input.audience.org },
        mode: input.mode,
        ...(input.expiresAt !== null ? { expiresAt: input.expiresAt } : {}),
      };
      return one((await apiClient.post(BASE, body, QUIET)).data);
    }, true),

  revoke: (publicationId: string) =>
    harnessCall(async () => one((await apiClient.delete(`${BASE}/${encodeURIComponent(publicationId)}`, QUIET)).data), true),
};

/** The query service's codes as Node relays them (`HTTP_AGENT_HARNESS_<CODE>`, upper-cased): the `publication` group. */
export const PUBLICATION_REFUSALS: Readonly<Record<ErrorCodeOf<'publication'>, string>> = {
  PUBLICATION_FORBIDDEN: 'publications.refusal.forbidden',
  PUBLICATION_INVALID: 'publications.refusal.invalid',
  PUBLICATION_NOT_FOUND: 'publications.refusal.notFound',
  PUBLICATIONS_DISABLED: 'publications.refusal.disabled',
  PUBLICATIONS_UNAVAILABLE: 'publications.refusal.unavailable',
  PUBLICATION_ANSWER_ONLY: 'publications.refusal.answerOnly',
  PUBLICATION_REFUSED: 'publications.refusal.refused',
  PUBLICATIONS_INVALID_RESPONSE: 'publications.refusal.unavailable',
};

/** Whether a publication still grants anything now. */
export const isLive = (publication: Publication, now: number): boolean =>
  publication.revokedAt === null && (publication.expiresAt === null || publication.expiresAt > now);

/**
 * Whether the collection header offers "Publish": the org's Labs flag, a root
 * collection, and an owner or admin viewing it (the query service decides again).
 */
export const canPublishCollection = (gate: {
  flagOn: boolean;
  isRootCollection: boolean;
  isOwner: boolean;
  isAdmin: boolean | null;
}): boolean => gate.flagOn && gate.isRootCollection && (gate.isOwner || gate.isAdmin === true);
