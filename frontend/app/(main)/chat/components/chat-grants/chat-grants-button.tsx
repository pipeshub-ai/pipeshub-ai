'use client';

import { Box, Flex, IconButton, Popover, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { overlayWidth, touchTarget } from '@/app/components/ui/responsive';
import { CHAT_GRANTS_BUTTON_ID, CHAT_GRANTS_PANEL_ID, focusChatGrantsButton, useChatGrantsStore } from './chat-grants-store';
import { ChatGrantsList } from './chat-grants-list';
import { useChatGrants } from './use-chat-grants';

/**
 * "Permissions in this chat" in the chat's top bar: shown to the chat's owner once the chat holds an "Allow for this
 * chat", and opens the list beside it (a bottom sheet on a phone). Removing the last one keeps the open list, which
 * then says nothing is allowed; once it closes, the button goes until something is allowed again.
 */
export function ChatGrantsButton({ isMobile }: { isMobile: boolean }) {
  const { t } = useTranslation();
  const { session, grants } = useChatGrants(true);
  const conversationId = session?.sessionId ?? null;
  const open = useChatGrantsStore((s) => conversationId !== null && s.openFor === conversationId);
  if (!conversationId || (grants.length === 0 && !open)) return null;

  const title = t('ui.grants.title');
  const setOpen = (next: boolean) => {
    const store = useChatGrantsStore.getState();
    if (next) store.open(conversationId);
    else store.close();
  };

  const trigger = (
    <IconButton
      id={CHAT_GRANTS_BUTTON_ID}
      data-testid="chat-grants-button"
      size="2"
      variant={open ? 'soft' : 'ghost'}
      color="gray"
      aria-label={title}
      aria-haspopup="dialog"
      aria-expanded={open}
      {...(open ? { 'aria-controls': CHAT_GRANTS_PANEL_ID } : {})}
      onClick={isMobile ? () => setOpen(!open) : undefined}
      style={{ margin: 0, ...touchTarget(isMobile) }}
    >
      <MaterialIcon name="verified_user" size={18} color="var(--gray-11)" />
    </IconButton>
  );

  if (isMobile) {
    return (
      <>
        {trigger}
        <MobileBottomSheet
          open={open}
          onOpenChange={(next) => {
            if (next) return;
            setOpen(false);
            focusChatGrantsButton();
          }}
          title={title}
        >
          <Box id={CHAT_GRANTS_PANEL_ID} data-testid="chat-grants-panel" style={{ padding: 'var(--space-2) var(--space-4) var(--space-5)' }}>
            <ChatGrantsList isMobile />
          </Box>
        </MobileBottomSheet>
      </>
    );
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Tooltip content={title}>
        <Popover.Trigger>{trigger}</Popover.Trigger>
      </Tooltip>
      <Popover.Content
        id={CHAT_GRANTS_PANEL_ID}
        data-testid="chat-grants-panel"
        aria-label={title}
        align="end"
        sideOffset={6}
        style={{ width: overlayWidth(380), padding: 'var(--space-3) var(--space-4)' }}
      >
        <Flex direction="column" gap="2">
          <Text as="div" size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Text>
          <Box style={{ maxHeight: '50vh', overflowY: 'auto' }} className="no-scrollbar">
            <ChatGrantsList isMobile={false} />
          </Box>
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
