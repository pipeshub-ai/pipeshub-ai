'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export type ReadOnlyBannerReason = 'readOnly' | 'accessLost' | 'ownerInactive';

interface ReadOnlyBannerProps {
  reason: ReadOnlyBannerReason;
  /** Owner's display name, when the server provided it. */
  ownerName?: string;
  /** Whether the transcript is on screen; when it is not, the lost-access text must not promise it is kept. */
  hasMessages?: boolean;
}

/** Sits above the (absent) composer and says why the user cannot continue the chat. */
export function ReadOnlyBanner({ reason, ownerName, hasMessages = true }: ReadOnlyBannerProps) {
  const { t } = useTranslation();
  const text =
    reason === 'accessLost'
      ? t(hasMessages ? 'chat.collab.accessLostBanner' : 'chat.collab.accessLostBannerEmpty')
      : reason === 'ownerInactive'
        ? t('chat.collab.errors.OWNER_INACTIVE')
        : ownerName
        ? t('chat.collab.readOnlyBannerOwner', { name: ownerName })
        : t('chat.collab.readOnlyBanner');
  return (
    <Flex
      role="status"
      align="center"
      gap="2"
      data-testid="read-only-banner"
      style={{
        width: '100%',
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--slate-3)',
        border: '1px solid var(--slate-6)',
        marginBottom: 'var(--space-2)',
      }}
    >
      <span aria-hidden style={{ display: 'inline-flex' }}>
        <MaterialIcon name={reason === 'readOnly' ? 'visibility' : 'lock'} size={16} color="var(--slate-11)" />
      </span>
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {text}
      </Text>
    </Flex>
  );
}
