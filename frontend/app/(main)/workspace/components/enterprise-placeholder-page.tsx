'use client';

import { useId } from 'react';
import { Link } from '@/lib/navigation';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ENTERPRISE_OVERVIEW_ROUTE, enterpriseContactUrl } from '@/lib/constants/enterprise';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EnterpriseGlimpse, type Glimpse } from './enterprise-glimpse';
import { useEnterpriseAdminGate } from './use-enterprise-admin-gate';

export interface EnterprisePlaceholderPageProps {
  /** Which Enterprise page this is, as named in the demo link's tags. */
  feature: string;
  /** The page's own title, as the Enterprise Edition names it. */
  title: string;
  subtitle?: string;
  /** What is available in the Enterprise Edition. */
  heading: string;
  /** One line on what the feature does. */
  message: string;
  /** What the feature gives an admin, a few short lines. */
  benefits: string[];
  /** Other Enterprise pages worth a look. */
  related: { label: string; href: string }[];
  /** A blurred look at the real page, fading into the message. */
  glimpse: Glimpse;
}

/**
 * Stand-in for a settings page that only the Enterprise Edition has, so sidebar links and
 * bookmarks land on an explanation instead of a 404: a blurred glimpse of the real page, running
 * out into a message laid out like `EntityEmptyState`, so it reads as part of the product.
 */
export function EnterprisePlaceholderPage({
  feature,
  title,
  subtitle,
  heading,
  message,
  benefits,
  related,
  glimpse,
}: EnterprisePlaceholderPageProps) {
  const { t } = useTranslation();
  const benefitsTitleId = useId();
  const isMobile = useIsMobile();
  const isAdmin = useEnterpriseAdminGate();

  if (!isAdmin) {
    return null;
  }

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingInline: pagePaddingX(40),
      }}
    >
      <Flex
        direction="column"
        gap="1"
        style={{ paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP, paddingBottom: 24 }}
      >
        <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {title}
        </Heading>
        {subtitle && (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {subtitle}
          </Text>
        )}
      </Flex>

      <EnterpriseGlimpse glimpse={glimpse} height={isMobile ? 220 : 300} />

      {/* Starts inside the glimpse's fade, so the page reads as running out into the message. */}
      <Flex
        direction="column"
        align="center"
        gap="4"
        style={{
          position: 'relative',
          marginTop: isMobile ? -48 : -72,
          padding: 'var(--space-6)',
          paddingBottom: 'var(--space-9)',
        }}
      >
        {/* Decorative; the icon is a text ligature that screen readers would otherwise read out. */}
        <Flex
          aria-hidden="true"
          align="center"
          justify="center"
          style={{
            width: '64px',
            height: '64px',
            borderRadius: '9999px',
            backgroundColor: 'var(--slate-3)',
          }}
        >
          <MaterialIcon name="workspace_premium" size={28} color="var(--slate-9)" />
        </Flex>

        <Flex direction="column" align="center" gap="2" style={{ maxWidth: '420px', textAlign: 'center' }}>
          <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)', textWrap: 'balance' }}>
            {heading}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {message}
          </Text>
        </Flex>

        {benefits.length > 0 && (
          <Flex
            direction="column"
            gap="3"
            style={{
              width: '100%',
              maxWidth: '440px',
              boxSizing: 'border-box',
              padding: 'var(--space-4)',
              border: '1px solid var(--gray-a5)',
              borderRadius: 'var(--radius-3)',
            }}
          >
            <Text id={benefitsTitleId} size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.enterprise.benefitsTitle')}
            </Text>
            <Flex asChild direction="column" gap="2">
              <ul aria-labelledby={benefitsTitleId} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {benefits.map((benefit) => (
                  <Flex asChild key={benefit} gap="2" align="start">
                    <li>
                      <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0, marginTop: 2 }}>
                        <MaterialIcon name="check" size={16} color="var(--accent-11)" />
                      </span>
                      <Text size="2" style={{ color: 'var(--slate-12)' }}>
                        {benefit}
                      </Text>
                    </li>
                  </Flex>
                ))}
              </ul>
            </Flex>
          </Flex>
        )}

        <Flex align="center" justify="center" gap="3" wrap="wrap" style={{ marginTop: 'var(--space-2)' }}>
          <Button asChild size="2" className="touch-hit">
            <a href={enterpriseContactUrl(feature)} target="_blank" rel="noopener noreferrer">
              {/* The icon is a text ligature; hide it so the link is named by its label alone. */}
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon name="event" size={16} color="currentColor" />
              </span>
              {t('workspace.enterprise.contact')}
            </a>
          </Button>
          <Button asChild size="2" variant="outline" color="gray" className="touch-hit">
            <Link href={`${ENTERPRISE_OVERVIEW_ROUTE}/`}>{t('workspace.enterprise.seeWhatsInEnterprise')}</Link>
          </Button>
        </Flex>

        {related.length > 0 && (
          <Flex asChild align="center" justify="center" gap="2" wrap="wrap" style={{ marginTop: 'var(--space-2)' }}>
            <nav aria-label={t('workspace.enterprise.relatedTitle')}>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {t('workspace.enterprise.relatedTitle')}
              </Text>
              {related.map(({ label, href }) => (
                <Button key={href} asChild size="1" variant="soft" color="gray" radius="full" className="touch-hit">
                  <Link href={href}>{label}</Link>
                </Button>
              ))}
            </nav>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
