'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useBrand } from '@/lib/branding';

const linkStyle: React.CSSProperties = {
  color: 'var(--gray-9)',
  textDecoration: 'none',
};

export function OnboardingBrandFooter() {
  const { t } = useTranslation();
  const brand = useBrand();
  const docsUrl = brand.docsHref();
  const privacyHref = brand.privacyUrl || '/privacy';

  return (
    <Flex
      justify="center"
      align="center"
      style={{ padding: '8px 0 24px', flexShrink: 0 }}
    >
      <Text size="1" style={{ color: 'var(--gray-9)', textAlign: 'center' }}>
        {t('onboarding.footer.copyright')}
        {' '}&nbsp;·&nbsp;{' '}
        <a href={privacyHref} style={linkStyle}>
          {t('onboarding.footer.privacyPolicy')}
        </a>
        {brand.websiteUrl ? (
          <>
            {' '}&nbsp;·&nbsp;{' '}
            <a
              href={brand.websiteUrl}
              target="_blank"
              rel="noreferrer"
              style={linkStyle}
            >
              {t('onboarding.footer.website')}
            </a>
          </>
        ) : null}
        {docsUrl ? (
          <>
            {' '}&nbsp;·&nbsp; {t('onboarding.footer.visitDocs')}{' '}
            <a
              href={docsUrl}
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent-11)', textDecoration: 'none' }}
            >
              {t('onboarding.footer.docsLabel')}
            </a>{' '}
            {t('onboarding.footer.toLearnMore')}
          </>
        ) : null}
      </Text>
    </Flex>
  );
}
