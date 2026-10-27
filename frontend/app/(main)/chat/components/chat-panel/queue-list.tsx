'use client';

import { useId, useState } from 'react';
import { Button, Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { chatExtension, chatSlots } from '@/config';
import type { QueueListExtension } from '@/app/(main)/chat/chat-extension';
import { useChatStore } from '@/app/(main)/chat/store';
import type { QueuedSend } from '@/app/(main)/chat/types';
import { clearQueue, editQueued, queueOf, removeQueued, retryQueued } from '../../utils/queued-send';

/**
 * The questions waiting their turn, above the box: the slot's, then its paired slot's, each in the order they go.
 * Each can be edited back into an empty box, removed, or tried again once refused. The list folds down to its title, so
 * a long queue never hides the chat.
 */
export function QueueList({ slotId }: { slotId: string | null }) {
  const { t } = useTranslation();
  const pairedId = useChatStore((s) => (slotId ? chatExtension.queue.pairedSlotId(s.slots, slotId) : null));
  const ownQueue = useChatStore((s) => (slotId ? s.slots[slotId]?.queue : undefined));
  const pairedQueue = useChatStore((s) => (pairedId ? s.slots[pairedId]?.queue : undefined));
  const boxHasText = useChatStore((s) => (slotId ? s.slots[slotId]?.composerHasText === true : false));
  const view = chatSlots.useQueueList({ slotId, pairedSlotId: pairedId });
  const [open, setOpen] = useState(true);
  const listId = useId();

  const rows = [
    ...(slotId ? queueOf({ queue: ownQueue }).map((item) => ({ slotId, item, paired: false })) : []),
    ...(pairedId ? queueOf({ queue: pairedQueue }).map((item) => ({ slotId: pairedId, item, paired: true })) : []),
  ];
  if (rows.length === 0) return null;

  const clearAll = () => {
    if (slotId) clearQueue(slotId);
    if (pairedId) clearQueue(pairedId);
  };

  return (
    <Flex direction="column" gap="1" data-testid="queue-list" style={BOX}>
      {/* The hint wraps beside the title; where even that is too narrow, Clear all moves down, keeping the right edge. */}
      <Flex align="center" gap="2" wrap="wrap">
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <MaterialIcon name="schedule_send" size={16} color="var(--slate-11)" />
          <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('chat.queue.title', { count: rows.length })}
          </Text>
        </Flex>
        <Text size="1" style={{ color: 'var(--slate-11)', flex: '1 1 8rem', minWidth: 0 }}>
          {view.hint ?? t('chat.queue.hint')}
        </Text>
        <Flex align="center" gap="2" style={{ marginLeft: 'auto' }}>
          <Button size="1" variant="ghost" color="gray" onClick={clearAll} data-testid="queue-clear" style={FLUSH}>
            {t('chat.queue.clearAll')}
          </Button>
          <IconButton
            size="1"
            variant="ghost"
            color="gray"
            aria-expanded={open}
            aria-controls={listId}
            aria-label={open ? t('chat.queue.collapse') : t('chat.queue.expand')}
            title={open ? t('chat.queue.collapse') : t('chat.queue.expand')}
            onClick={() => setOpen((was) => !was)}
            data-testid="queue-toggle"
            style={FLUSH}
          >
            <MaterialIcon name={open ? 'expand_more' : 'expand_less'} size={16} />
          </IconButton>
        </Flex>
      </Flex>
      <ul id={listId} hidden={!open} aria-label={t('chat.queue.listLabel')} style={LIST}>
        {rows.map(({ slotId: rowSlotId, item, paired }) => (
          <QueueRow
            key={item.clientMessageId}
            item={item}
            text={view.rowText(item)}
            paired={paired}
            rowTags={view.rowTags}
            boxHasText={boxHasText}
            onEdit={() => editQueued(rowSlotId, item.clientMessageId)}
            onRemove={() => removeQueued(rowSlotId, item.clientMessageId)}
            onRetry={() => retryQueued(rowSlotId, item.clientMessageId)}
          />
        ))}
      </ul>
    </Flex>
  );
}

interface QueueRowProps {
  item: QueuedSend;
  text: string;
  paired: boolean;
  rowTags: QueueListExtension['rowTags'];
  boxHasText: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onRetry: () => void;
}

function QueueRow({ item, text, paired, rowTags, boxHasText, onEdit, onRemove, onRetry }: QueueRowProps) {
  const { t } = useTranslation();
  const files = item.attachments?.length ?? 0;
  // An edit takes the text back into the box, which must be empty: it never mixes with a draft.
  const editLabel = boxHasText ? t('chat.queue.editBusy') : t('chat.queue.edit');
  return (
    <li data-testid="queued-row" data-failed={item.failed ? 'true' : undefined} style={ROW}>
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Text size="2" title={text} style={TEXT}>
          {text}
        </Text>
        {rowTags(item, {
          paired,
          files: files > 0 ? <QueueTag icon="attach_file" text={String(files)} label={t('chat.queue.files', { count: files })} /> : null,
        })}
        <span title={editLabel} style={{ display: 'inline-flex' }}>
          <IconButton size="1" variant="ghost" color="gray" aria-label={editLabel} disabled={boxHasText} onClick={onEdit} style={FLUSH}>
            <MaterialIcon name="edit" size={14} />
          </IconButton>
        </span>
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          aria-label={t('chat.queue.remove')}
          title={t('chat.queue.remove')}
          onClick={onRemove}
          style={FLUSH}
        >
          <MaterialIcon name="close" size={14} />
        </IconButton>
      </Flex>
      {item.failed && (
        <Flex align="center" gap="2" wrap="wrap">
          <Text size="1" color="red" role="alert" style={{ flex: '1 1 12rem', minWidth: 0 }}>
            {item.failed}
          </Text>
          <Button size="1" variant="soft" onClick={onRetry}>
            {t('chat.queue.retry')}
          </Button>
        </Flex>
      )}
    </li>
  );
}

/** An icon with an optional count; the label says it for a screen reader. */
export function QueueTag({ icon, text, label }: { icon: string; text?: string; label: string }) {
  return (
    <Flex align="center" gap="1" role="img" aria-label={label} title={label} style={{ flexShrink: 0, color: 'var(--slate-11)' }}>
      <MaterialIcon name={icon} size={14} color="var(--slate-11)" />
      {text && (
        <Text size="1" aria-hidden>
          {text}
        </Text>
      )}
    </Flex>
  );
}

// A card tucked onto the top of the box below it: a little narrower, rounded on top only, with no gap and no bottom
// edge, so the two read as one.
const BOX = {
  width: 'calc(100% - 2 * var(--space-3))',
  margin: '0 auto',
  padding: '8px 12px',
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-4)',
  borderBottom: 'none',
  borderRadius: 'var(--radius-3) var(--radius-3) 0 0',
} as const;

// Five rows at most (the cap), so the list rarely scrolls; folded, only its title stays.
const LIST = { listStyle: 'none', margin: 0, padding: 0, maxHeight: '11rem', overflowY: 'auto' } as const;
const ROW = { display: 'flex', flexDirection: 'column', gap: 2, padding: '2px 0' } as const;
// Two lines at most, so a phone shows most of a question (no hover there for the title); a long word breaks.
const TEXT = {
  flex: '1 1 auto',
  minWidth: 0,
  color: 'var(--slate-12)',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
  overflowWrap: 'anywhere',
} as const;
// A ghost button's negative margins would reach past the list's edge and scroll it sideways.
const FLUSH = { margin: 0 } as const;
