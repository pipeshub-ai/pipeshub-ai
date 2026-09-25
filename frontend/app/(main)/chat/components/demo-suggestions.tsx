'use client';

import React from 'react';
import { Flex, Text, Link } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { SuggestionChip } from './suggestion-chip';
import { ChatSuggestion } from '@/chat/types';
import { buildConnectorsUrl } from '@/app/(main)/workspace/connectors/utils/build-connectors-url';
import { useRestrictedQuestionAccess } from '@/app/(main)/workspace/connectors/demo-data/use-restricted-question';
import { useDemoSwitch } from '@/app/(main)/workspace/connectors/demo-data/use-demo-switch';

const EMAIL_SLOT = '\u2063';

interface DemoSuggestionsProps {
  isAdmin: boolean | null;
  isMobile: boolean;
  onPick: (suggestion: ChatSuggestion) => void;
}

/**
 * New-chat landing extras while the Acme Corp demo connector is active: the
 * questions the sample data was written to answer, and the way out of the
 * demo (connect a real source).
 */
export function DemoSuggestions({ isAdmin, isMobile, onPick }: DemoSuggestionsProps) {
  const { t } = useTranslation();
  const access = useRestrictedQuestionAccess();
  const { hideWithUndo, busy: switchBusy } = useDemoSwitch();
  const map = t('chat.demoSuggestions', { returnObjects: true }) as Record<
    string,
    { text: string; icons: ChatSuggestion['icons']; restricted?: boolean }
  >;
  const suggestions = Object.entries(map).map(([id, item]) => ({
    id,
    text: item.text,
    icons: item.icons,
    // Only for someone who will get nothing back; the pricing committee sees a plain chip.
    locked: !!item.restricted && access?.canSee === false,
  }));
  // Only an admin set up the sample accounts and knows their password.
  const readerEmail = isAdmin === true ? access?.readerEmail : null;
  let lockedHint: React.ReactNode = t('chat.demoRestrictedHint');
  if (readerEmail) {
    // Translated as one sentence; the address is kept on one line, not broken at its hyphen.
    const [before, after = ''] = t('chat.demoRestrictedHintSignIn', { email: EMAIL_SLOT }).split(EMAIL_SLOT);
    lockedHint = (
      <>
        {before}
        <span style={{ whiteSpace: 'nowrap' }}>{readerEmail}</span>
        {after}
      </>
    );
  }

  return (
    <Flex direction="column" align="center" gap="3" style={{ width: '100%', marginTop: 'var(--space-5)' }}>
      <Text size="1" style={{ color: 'var(--slate-11)', textAlign: 'center' }}>
        {t('chat.demoBanner')}{' '}
        <Link href={buildConnectorsUrl(isAdmin)} size="1" weight="medium">
          {t('chat.demoConnectYourOwn')}
        </Link>
        {' · '}
        <Link asChild size="1" weight="medium">
          <button
            type="button"
            disabled={switchBusy}
            onClick={() => void hideWithUndo()}
            style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
          >
            {t('chat.demoHide')}
          </button>
        </Link>
      </Text>
      <Flex
        direction={isMobile ? 'column' : 'row'}
        wrap="wrap"
        justify="center"
        gap="2"
        style={{ width: '100%' }}
      >
        {suggestions.map(({ locked, ...s }) => {
          const chip = (
            <SuggestionChip
              key={s.id}
              text={s.text}
              icons={s.icons}
              fullWidth={isMobile}
              locked={locked}
              onClick={() => onPick(s)}
            />
          );
          if (!locked) return chip;
          // Said before the click: once asked, the answer alone reads as a failure.
          return (
            <Flex key={s.id} direction="column" align="center" gap="1" style={{ width: isMobile ? '100%' : undefined }}>
              {chip}
              <Text size="1" style={{ color: 'var(--slate-10)', textAlign: 'center', maxWidth: 420 }}>
                {lockedHint}
              </Text>
            </Flex>
          );
        })}
      </Flex>
    </Flex>
  );
}
