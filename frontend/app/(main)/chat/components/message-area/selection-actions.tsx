'use client';

import React, { forwardRef, useState } from 'react';
import { Button, Flex, Separator, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface SelectionActionsProps extends Omit<React.ComponentPropsWithoutRef<'div'>, 'color'> {
  /** The words selected: what Copy copies. */
  text: string;
  onReply?: () => void;
  onAsk?: () => void;
}

/**
 * The bar over selected text: Reply and Ask {{productName}} for someone who may write, Copy for everyone. Whoever draws
 * it says what the first two do and where it sits. Pressing on it keeps the selection, which the browser would clear.
 */
export const SelectionActions = forwardRef<HTMLDivElement, SelectionActionsProps>(function SelectionActions(
  { text, onReply, onAsk, style, ...rest },
  ref,
) {
  const { t } = useTranslation();
  // Other words selected next have not been copied.
  const [copied, setCopied] = useState<string | null>(null);
  return (
    <Flex
      {...rest}
      ref={ref}
      role="group"
      align="center"
      aria-label={t('chat.selectionActions.label')}
      onMouseDown={(e) => e.preventDefault()}
      style={{
        padding: 'calc(var(--space-1) * 0.75)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--surface-subtle)',
        border: '1px solid var(--border-default)',
        boxShadow: 'var(--shadow-4)',
        whiteSpace: 'nowrap',
        userSelect: 'none',
        ...style,
      }}
    >
      {onReply && <Action icon="reply" label={t('chat.selectionActions.reply')} onClick={onReply} />}
      {onReply && onAsk && <Divider />}
      {onAsk && <Action icon="auto_awesome" label={t('chat.selectionActions.ask')} onClick={onAsk} />}
      {(onReply || onAsk) && <Divider />}
      <Action
        icon={copied === text ? 'check' : 'content_copy'}
        label={copied === text ? t('chat.selectionActions.copied') : t('chat.copy')}
        onClick={() => {
          void navigator.clipboard
            ?.writeText(text)
            .then(() => setCopied(text))
            .catch(() => {});
        }}
      />
    </Flex>
  );
});

/** The line between two actions. */
function Divider() {
  return (
    <Separator
      orientation="vertical"
      size="1"
      style={{ marginInline: 'calc(var(--space-1) / 2)', backgroundColor: 'var(--fill-active)' }}
    />
  );
}

/**
 * One action: a ghost button 26 px tall in regular weight, so the bar is 34 px; tinted on hover, and on a phone a
 * hidden 44 px target around it (globals.css).
 */
function Action({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      color="gray"
      size="1"
      onClick={onClick}
      className="ghost-box selection-action"
      style={{
        gap: 'var(--space-1)',
        height: 'calc(var(--space-5) + var(--space-1) / 2)',
        paddingBlock: 0,
        paddingInline: 'var(--space-2)',
        borderRadius: 'var(--radius-2)',
        cursor: 'pointer',
      }}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
        <MaterialIcon name={icon} size={13} color="var(--text-secondary)" />
      </span>
      <Text size="1" weight="regular" style={{ color: 'var(--text-primary)' }}>
        {label}
      </Text>
    </Button>
  );
}
