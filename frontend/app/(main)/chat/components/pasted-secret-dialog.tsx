'use client';

import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Where a person keeps a key for their actions: the personal actions page, with its credential dialog. */
export const PERSONAL_CREDENTIALS_PATH = '/workspace/actions/personal';

interface PastedSecretDialogProps {
  open: boolean;
  /** Sends the message with each secret replaced by the mask: what the server would keep anyway. */
  onSendWithout: () => void;
  /** Takes the secret out of the draft and opens the personal actions page to store it as a credential. */
  onSaveAsCredential: () => void;
  onKeepEditing: () => void;
}

/**
 * SEC-CR-07: the composer found what looks like a password, key or token. A
 * secret sent in chat would be kept in the conversation and seen by the model,
 * so the server removes it whatever happens here; this says so before send and
 * offers the credential store, which the agent's tools use without the model
 * ever seeing the value.
 */
export function PastedSecretDialog({ open, onSendWithout, onSaveAsCredential, onKeepEditing }: PastedSecretDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onKeepEditing()}>
      <Dialog.Content style={{ maxWidth: '30rem', width: '100%', padding: 'var(--space-5)' }}>
        <Flex align="center" gap="2" mb="2">
          <MaterialIcon name="key" size={20} color="var(--amber-11)" />
          <Dialog.Title style={{ margin: 0 }}>{t('chat.pastedSecret.title')}</Dialog.Title>
        </Flex>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)' }}>
          {t('chat.pastedSecret.description')}
        </Dialog.Description>
        <Text as="p" size="2" mt="3" style={{ color: 'var(--slate-11)' }}>
          {t('chat.pastedSecret.credentialHint')}
        </Text>
        <Flex gap="3" mt="5" justify="end" wrap="wrap">
          <Button variant="soft" color="gray" onClick={onKeepEditing} style={{ minHeight: 44 }}>
            {t('chat.pastedSecret.keepEditing')}
          </Button>
          <Button variant="soft" onClick={onSaveAsCredential} style={{ minHeight: 44 }}>
            {t('chat.pastedSecret.saveAsCredential')}
          </Button>
          <Button onClick={onSendWithout} style={{ minHeight: 44 }}>
            {t('chat.pastedSecret.sendWithout')}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
