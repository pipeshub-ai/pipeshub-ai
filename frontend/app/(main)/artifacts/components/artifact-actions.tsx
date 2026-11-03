'use client';

import type { TFunction } from 'i18next';
import { ItemActionMenu } from '@/knowledge-base/components/item-action-menu';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { ArtifactListItem } from '../types';

export interface ArtifactActionHandlers {
  onPreview: (item: ArtifactListItem) => void;
  onDownload: (item: ArtifactListItem) => void;
  onOpenChat: (item: ArtifactListItem) => void;
  onShare: (item: ArtifactListItem) => void;
  onDelete: (item: ArtifactListItem) => void;
}

/** One "…" menu per artifact, shared by the list and grid views. */
export function ArtifactActionMenu({
  item,
  handlers,
  t,
}: {
  item: ArtifactListItem;
  handlers: ArtifactActionHandlers;
  t: TFunction;
}) {
  const isMobile = useIsMobile();
  return (
    <ItemActionMenu
      triggerSize={isMobile ? '4' : '1'}
      triggerLabel={t('artifacts.moreActionsFor', { name: item.name })}
      actions={[
        { icon: 'visibility', label: t('artifacts.preview'), onClick: () => handlers.onPreview(item) },
        { icon: 'download', label: t('action.download'), onClick: () => handlers.onDownload(item) },
        // A title means the caller can open that chat; without one the link would 404.
        !!item.conversationTitle && {
          icon: 'chat',
          label: t('artifacts.openInChat'),
          onClick: () => handlers.onOpenChat(item),
        },
        item.role === 'OWNER' && {
          icon: 'person_add',
          label: t('action.share'),
          onClick: () => handlers.onShare(item),
        },
        item.role === 'OWNER' && {
          icon: 'delete',
          label: t('action.delete'),
          color: 'red',
          separatorBefore: true,
          onClick: () => handlers.onDelete(item),
        },
      ]}
    />
  );
}
