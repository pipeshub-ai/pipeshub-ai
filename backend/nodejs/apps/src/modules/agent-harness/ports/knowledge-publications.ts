/**
 * Knowledge publications (P2.13, D21). The query service owns publications in
 * the graph and decides every request again with the caller's own credential;
 * Node checks the org's Labs flag, validates the body and forwards the
 * caller's Authorization header, nothing else.
 */

/** Labs `ENABLE_KNOWLEDGE_PUBLICATIONS` (org). Unreadable reads as off. */
export interface KnowledgePublicationsSetting {
  enabled(orgId: string): Promise<boolean>;
}

export type ContainerKind = 'kb' | 'recordGroup';
export type PublicationMode = 'answer_only' | 'full';

export interface PublicationAudience {
  userIds: string[];
  teamIds: string[];
  org: boolean;
}

export interface CreatePublicationBody {
  containerKind: ContainerKind;
  containerId: string;
  audience: PublicationAudience;
  mode: PublicationMode;
  expiresAt?: number;
}

export interface PublicationView {
  id: string;
  containerKind: ContainerKind;
  containerId: string;
  audience: PublicationAudience;
  mode: PublicationMode;
  createdBy: string;
  createdAt: number;
  expiresAt: number | null;
  revokedAt: number | null;
  revokedBy: string | null;
}

/** What the query service answered: a value, or its status and typed code. */
export type AuthorityAnswer<T> =
  | { ok: true; value: T }
  | { ok: false; status: number; code: string; message: string };

export interface PublicationAuthority {
  create(
    authorization: string,
    body: CreatePublicationBody,
  ): Promise<AuthorityAnswer<PublicationView>>;
  list(
    authorization: string,
    containerKind: ContainerKind,
    containerId: string,
  ): Promise<AuthorityAnswer<PublicationView[]>>;
  revoke(
    authorization: string,
    publicationId: string,
  ): Promise<AuthorityAnswer<PublicationView>>;
}
