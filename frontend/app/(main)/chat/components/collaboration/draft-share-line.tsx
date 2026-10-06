'use client';

import { Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useDraftShareStore } from '../../draft-share-store';

interface DraftShareLineProps {
  onEdit(): void;
}

/** Above the composer of a new chat: who will get access when the first message goes out. */
export function DraftShareLine({ onEdit }: DraftShareLineProps) {
  const { t } = useTranslation();
  const principals = useDraftShareStore((s) => s.principals);
  const remove = useDraftShareStore((s) => s.remove);
  if (principals.length === 0) return null;
  const level = (l: 'read' | 'write') => t(l === 'write' ? 'chat.collab.share.roleWrite' : 'chat.collab.share.roleRead');

  return (
    <Flex
      align="center"
      gap="2"
      wrap="wrap"
      data-testid="draft-share-line"
      style={{ padding: 'var(--space-1) var(--space-2)', minWidth: 0 }}
    >
      <button
        type="button"
        onClick={onEdit}
        aria-label={t('chat.collab.draft.edit')}
        data-testid="draft-share-edit"
        style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}
      >
        <MaterialIcon name="group_add" size={16} color="var(--slate-11)" />
        <Text size="1" color="gray">
          {t('chat.collab.draft.willShare')}
        </Text>
      </button>
      {principals.map((p) => (
        <Flex
          key={`${p.type}:${p.id}`}
          align="center"
          gap="1"
          data-testid="draft-share-entry"
          style={{
            border: '1px solid var(--slate-6)',
            borderRadius: 'var(--radius-3)',
            padding: '0 4px 0 8px',
            minWidth: 0,
            maxWidth: '100%',
          }}
        >
          <Text
            size="1"
            style={{ color: 'var(--slate-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}
          >
            {t('chat.collab.draft.entry', { name: p.name, level: level(p.level) })}
          </Text>
          <IconButton
            size="1"
            variant="ghost"
            color="gray"
            aria-label={t('chat.collab.draft.remove', { name: p.name })}
            onClick={() => remove(p.type, p.id)}
          >
            <MaterialIcon name="close" size={14} />
          </IconButton>
        </Flex>
      ))}
    </Flex>
  );
}
