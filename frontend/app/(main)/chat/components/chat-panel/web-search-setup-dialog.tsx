'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

/**
 * Shown when someone turns web search on before an admin has chosen a search provider.
 * An admin gets a way to the settings page; anyone else is told whom to ask.
 */
export function WebSearchSetupDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content maxWidth="420px" data-testid="web-search-setup-dialog">
        <Dialog.Title size="4">{t('chat.webSearchSetup.title')}</Dialog.Title>
        <Dialog.Description size="2" color="gray">
          {isAdmin ? t('chat.webSearchSetup.adminBody') : t('chat.webSearchSetup.memberBody')}
        </Dialog.Description>
        {isAdmin ? null : (
          <Text as="p" size="1" color="gray" mt="3">
            {t('chat.webSearchSetup.memberHint')}
          </Text>
        )}
        <Flex gap="3" mt="4" justify="end" wrap="wrap">
          <Dialog.Close>
            <Button variant="soft" color="gray">
              {isAdmin ? t('chat.webSearchSetup.notNow') : t('chat.webSearchSetup.gotIt')}
            </Button>
          </Dialog.Close>
          {isAdmin && (
            <Button
              onClick={() => {
                onOpenChange(false);
                router.push('/workspace/web-search');
              }}
            >
              {t('chat.webSearchSetup.setUp')}
            </Button>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
