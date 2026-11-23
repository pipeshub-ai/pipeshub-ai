'use client';

import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Shown on the support and feedback forms when the workspace has no SMTP: the message goes out from the person's own email app. */
export function SmtpNotice({ supportEmail, isAdmin }: { supportEmail: string; isAdmin: boolean }) {
  const { t } = useTranslation();
  return (
    <Flex
      gap="3"
      align="start"
      style={{
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--amber-a3)',
        border: '1px solid var(--amber-a5)',
      }}
    >
      <MaterialIcon name="mail_lock" size={18} color="var(--amber-11)" />
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--amber-12)' }}>
          {t('support.details.smtpTitle')}
        </Text>
        <Text size="1" style={{ color: 'var(--amber-11)' }}>
          {t('support.details.smtpBody', { email: supportEmail })}{' '}
          {isAdmin ? t('support.details.smtpAdmin') : t('support.details.smtpMember')}
        </Text>
      </Flex>
    </Flex>
  );
}
