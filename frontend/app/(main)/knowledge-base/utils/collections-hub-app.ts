import { i18n } from '@/lib/i18n';
import type { KnowledgeHubNode } from '../types';

/** Decodes a JWT payload without verifying the signature (same shape as axios `decodeToken`). */
export function decodeJwtPayload(token: string | null): Record<string, unknown> | null {
  try {
    if (!token) return null;
    const parts = token.split('.');
    if (parts.length < 2) return null;
    let base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = base64.length % 4;
    if (pad) base64 += '='.repeat(4 - pad);
    const payload = typeof atob === 'function' ? atob(base64) : '';
    if (!payload) return null;
    return JSON.parse(payload) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function getOrgIdFromAccessToken(token: string | null): string | null {
  const payload = decodeJwtPayload(token);
  const orgId = payload?.orgId;
  return typeof orgId === 'string' && orgId.trim().length > 0 ? orgId.trim() : null;
}

/** Backend Collection app id when parent type is `app` (see knowledge_hub_router). */
export function buildCollectionsHubAppId(orgId: string): string {
  return `knowledgeBase_${orgId}`;
}

export function createSyntheticKbCollectionsHubApp(collectionAppId: string): KnowledgeHubNode {
  return {
    id: collectionAppId,
    name: i18n.t('nav.collections'),
    nodeType: 'app',
    parentId: null,
    origin: 'COLLECTION',
    connector: 'KB',
    subType: 'KB',
    hasChildren: true,
    permission: {
      role: 'OWNER',
      canEdit: true,
      canDelete: true,
    },
    sharingStatus: 'private',
  };
}

export type CollectionsHubBootstrapResult =
  | { ok: true; app: KnowledgeHubNode }
  | { ok: false; reason: 'missing_token' | 'missing_org_id' };

/**
 * Resolves the org-scoped KB Collections hub app node from the access token only
 * (no navigation list). Used for Collections view bootstrap and refresh fallbacks.
 */
export function getCollectionsHubBootstrapFromToken(accessToken: string | null): CollectionsHubBootstrapResult {
  if (!accessToken) {
    return { ok: false, reason: 'missing_token' };
  }
  const orgId = getOrgIdFromAccessToken(accessToken);
  if (!orgId) {
    return { ok: false, reason: 'missing_org_id' };
  }
  const hubId = buildCollectionsHubAppId(orgId);
  return { ok: true, app: createSyntheticKbCollectionsHubApp(hubId) };
}
