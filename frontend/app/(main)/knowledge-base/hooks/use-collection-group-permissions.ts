'use client';

import { useUserPermission } from '@/config';
import { isKbCollectionNode, type ReindexNode } from '../utils/reindex-label';

/** The caller's group permissions for the collection actions in item menus. */
export interface CollectionGroupPermissions {
  canEdit: boolean;
  canDelete: boolean;
  /** Reindexing a whole collection goes through the connector reindex route, which checks this. */
  canManageSync: boolean;
}

export function useCollectionGroupPermissions(): CollectionGroupPermissions {
  const canEdit = useUserPermission('editCollection');
  const canDelete = useUserPermission('deleteCollection');
  const canManageSync = useUserPermission('manageConnectorSync');
  return { canEdit, canDelete, canManageSync };
}

/** Reindex locks on the permission its route checks: connector sync for a collection, edit for anything inside one. */
export function isReindexPermissionLocked(
  node: ReindexNode,
  permissions: CollectionGroupPermissions | undefined,
): boolean {
  if (!permissions) return false;
  return isKbCollectionNode(node) ? !permissions.canManageSync : !permissions.canEdit;
}
