'use client';

import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EnterpriseGlimpse, type Glimpse } from './enterprise-glimpse';

const ENTERPRISE_CONTACT_URL = 'https://calendly.com/rishabh-pipeshub/30min';

export interface EnterprisePlaceholderPageProps {
  /** The page's own title, as the Enterprise Edition names it. */
  title: string;
  subtitle?: string;
  /** What is available in the Enterprise Edition. */
  heading: string;
  /** One line on what the feature does. */
  message: string;
  contactLabel: string;
  /** A blurred look at the real page, fading into the message. */
  glimpse: Glimpse;
}

/**
 * Stand-in for a settings page that only the Enterprise Edition has, so sidebar links and
 * bookmarks land on an explanation instead of a 404: a blurred glimpse of the real page, running
 * out into a message laid out like `EntityEmptyState`, so it reads as part of the product.
 */
export function EnterprisePlaceholderPage({ title, subtitle, heading, message, contactLabel, glimpse }: EnterprisePlaceholderPageProps) {
  const isMobile = useIsMobile();

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

      <EnterpriseGlimpse glimpse={glimpse} height={isMobile ? 200 : 260} />

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
        <Flex
          align="center"
          justify="center"
          style={{
            width: '64px',
            height: '64px',
            borderRadius: 'var(--radius-full)',
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

        <Button asChild size="2" className="touch-hit" style={{ marginTop: 'var(--space-2)' }}>
          <a href={ENTERPRISE_CONTACT_URL} target="_blank" rel="noreferrer">
            {/* The icon is a text ligature; hide it so the link is named by its label alone. */}
            <span aria-hidden="true" style={{ display: 'inline-flex' }}>
              <MaterialIcon name="calendar_month" size={16} color="currentColor" />
            </span>
            {contactLabel}
          </a>
        </Button>
      </Flex>
    </Flex>
  );
}
