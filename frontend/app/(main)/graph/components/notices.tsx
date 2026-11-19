'use client';

import { Button, Callout, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Notice, NoticeAction } from '../status';

const COLOUR = { info: 'gray', warning: 'amber', error: 'red' } as const;
const ICON = { info: 'info', warning: 'warning', error: 'error' } as const;

function NoticeRow({ notice, onAction, alert }: { notice: Notice; onAction?: (action: NoticeAction) => void; alert?: boolean }) {
  const { t } = useTranslation();
  return (
    <Callout.Root color={COLOUR[notice.level]} size="1" role={alert ? 'alert' : undefined}>
      <Callout.Icon>
        <span aria-hidden="true">
          <MaterialIcon name={ICON[notice.level]} size={16} />
        </span>
      </Callout.Icon>
      <Callout.Text>{t(notice.key)}</Callout.Text>
      {notice.action && onAction ? (
        <Button size="1" variant="soft" highContrast ml="3" onClick={() => onAction(notice.action!)}>
          {t(`graph.ui.action.${notice.action}`)}
        </Button>
      ) : null}
    </Callout.Root>
  );
}

/**
 * Status of the graph and of the access check. The polite region is always in the page, so the first message is
 * announced; an error is its own alert, not nested inside it.
 */
export function Notices({ notices, onAction }: { notices: Notice[]; onAction?: (action: NoticeAction) => void }) {
  return (
    <Flex direction="column" gap="2">
      <div role="status" aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        {notices
          .filter((n) => n.level !== 'error')
          .map((notice) => (
            <NoticeRow key={notice.key} notice={notice} onAction={onAction} />
          ))}
      </div>
      {notices
        .filter((n) => n.level === 'error')
        .map((notice) => (
          <NoticeRow key={notice.key} notice={notice} onAction={onAction} alert />
        ))}
    </Flex>
  );
}
