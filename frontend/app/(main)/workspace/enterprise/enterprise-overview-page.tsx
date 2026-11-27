'use client';

import type { CSSProperties, ReactNode } from 'react';
import { Link } from '@/lib/navigation';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Grid, Heading, Link as RadixLink, Table, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { enterpriseContactUrl } from '@/lib/constants/enterprise';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { useEnterpriseAdminGate } from '@/app/(main)/workspace/components/use-enterprise-admin-gate';
import { ENTERPRISE_FEATURES } from './enterprise-feature-pages';
import {
  COMMUNITY_PLAN_ITEMS,
  ENTERPRISE_OVERVIEW_GROUPS,
  ENTERPRISE_OVERVIEW_ITEMS,
  type EnterpriseOverviewItem,
} from './enterprise-overview';

const OVERVIEW = 'workspace.enterprise.overview';
const COMPARE_ID = 'enterprise-compare';

// Radix's Table.Root puts its props on a wrapper, so the table is named by its caption.
const VISUALLY_HIDDEN: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};

/** A Material icon that is decoration: its ligature text must not end up in a name. */
function Icon({ name, size = 18, color = 'var(--accent-11)' }: { name: string; size?: number; color?: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
      <MaterialIcon name={name} size={size} color={color} />
    </span>
  );
}

function TalkWithUs() {
  const { t } = useTranslation();
  return (
    <Button asChild size="2" className="touch-hit">
      <a href={enterpriseContactUrl('overview')} target="_blank" rel="noopener noreferrer">
        <Icon name="chat" size={16} color="currentColor" />
        {t('workspace.enterprise.contact')}
      </a>
    </Button>
  );
}

function PlanCard({ id, emphasis, children }: { id: string; emphasis?: boolean; children: ReactNode }) {
  return (
    <Flex
      asChild
      direction="column"
      gap="4"
      style={{
        padding: 'var(--space-5)',
        borderRadius: 'var(--radius-4)',
        border: `1px solid ${emphasis ? 'var(--accent-a6)' : 'var(--olive-4)'}`,
        background: emphasis
          ? 'linear-gradient(180deg, var(--accent-a3), var(--color-panel-solid) 120px)'
          : 'var(--color-panel-solid)',
        minWidth: 0,
      }}
    >
      <section aria-labelledby={id}>{children}</section>
    </Flex>
  );
}

function CheckList({ children }: { children: ReactNode }) {
  return (
    <Flex asChild direction="column" gap="3">
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>{children}</ul>
    </Flex>
  );
}

function CheckItem({ icon, muted, title, detail }: { icon: string; muted?: boolean; title: string; detail?: string }) {
  return (
    <Flex asChild gap="2" align="start">
      <li>
        <Box style={{ marginTop: 1 }}>
          <Icon name={icon} color={muted ? 'var(--slate-11)' : 'var(--accent-11)'} />
        </Box>
        <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
          <Text size="2" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Text>
          {detail && (
            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {detail}
            </Text>
          )}
        </Flex>
      </li>
    </Flex>
  );
}

function CommunityPlan() {
  const { t } = useTranslation();
  const id = 'enterprise-plan-community';
  return (
    <PlanCard id={id}>
      <Flex align="center" justify="between" gap="2" wrap="wrap">
        <Heading as="h2" id={id} size="4" weight="bold" style={{ color: 'var(--slate-12)' }}>
          {t(`${OVERVIEW}.plans.community.name`)}
        </Heading>
        <Badge size="1" variant="outline" color="gray" radius="full">
          {t(`${OVERVIEW}.plans.community.yourPlan`)}
        </Badge>
      </Flex>
      <Text size="2" style={{ color: 'var(--slate-11)', marginTop: 'calc(-1 * var(--space-2))' }}>
        {t(`${OVERVIEW}.plans.community.summary`)}
      </Text>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {t(`${OVERVIEW}.plans.community.lead`)}
      </Text>
      <CheckList>
        {COMMUNITY_PLAN_ITEMS.map((key) => (
          <CheckItem key={key} icon="check" muted title={t(`${OVERVIEW}.plans.community.items.${key}`)} />
        ))}
      </CheckList>
      {/* Where the other card has its button: this plan is already yours, so there is nothing to press. */}
      <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', marginTop: 'auto' }}>
        {t(`${OVERVIEW}.plans.community.current`)}
      </Text>
    </PlanCard>
  );
}

function EnterprisePlan() {
  const { t } = useTranslation();
  const id = 'enterprise-plan-enterprise';
  const highlights = ENTERPRISE_OVERVIEW_ITEMS.filter((item) => item.highlight);
  return (
    <PlanCard id={id} emphasis>
      <Flex align="center" gap="2">
        <Icon name="workspace_premium" size={20} />
        <Heading as="h2" id={id} size="4" weight="bold" style={{ color: 'var(--slate-12)' }}>
          {t(`${OVERVIEW}.plans.enterprise.name`)}
        </Heading>
      </Flex>
      <Text size="2" style={{ color: 'var(--slate-11)', marginTop: 'calc(-1 * var(--space-2))' }}>
        {t(`${OVERVIEW}.plans.enterprise.summary`)}
      </Text>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {t(`${OVERVIEW}.plans.enterprise.lead`)}
      </Text>
      <CheckList>
        {highlights.map((item) => (
          <CheckItem
            key={item.key}
            icon="check_circle"
            title={t(`${OVERVIEW}.items.${item.key}.name`)}
            detail={t(`${OVERVIEW}.items.${item.key}.description`)}
          />
        ))}
      </CheckList>
      <Flex align="center" gap="4" wrap="wrap" style={{ marginTop: 'auto', paddingTop: 'var(--space-1)' }}>
        <TalkWithUs />
        <RadixLink href={`#${COMPARE_ID}`} size="2" weight="medium" className="touch-hit">
          {t(`${OVERVIEW}.plans.enterprise.seeAll`)}
        </RadixLink>
      </Flex>
    </PlanCard>
  );
}

function CompareRow({ item, compact }: { item: EnterpriseOverviewItem; compact: boolean }) {
  const { t } = useTranslation();
  const name = t(`${OVERVIEW}.items.${item.key}.name`);
  return (
    <Table.Row data-testid="enterprise-compare-row">
      <Table.RowHeaderCell>
        <Flex direction="column" gap="1">
          <Flex align="center" gap="2">
            <Icon name={item.icon} />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {name}
            </Text>
          </Flex>
          <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 'normal' }}>
            {t(`${OVERVIEW}.items.${item.key}.description`)}
          </Text>
          {item.feature && (
            <RadixLink asChild size="1" weight="medium" style={{ alignSelf: 'flex-start' }}>
              <Link
                href={`${ENTERPRISE_FEATURES[item.feature].route}/`}
                aria-label={t(`${OVERVIEW}.seePreviewLabel`, { name })}
                className="touch-hit"
              >
                {t(`${OVERVIEW}.seePreview`)}
              </Link>
            </RadixLink>
          )}
        </Flex>
      </Table.RowHeaderCell>
      <Table.Cell data-testid="enterprise-compare-community">
        {item.inCommunity ? (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(`${OVERVIEW}.compare.communityHas.${item.key}`)}
          </Text>
        ) : (
          <>
            <Text size="2" aria-hidden="true" style={{ color: 'var(--slate-11)' }}>
              —
            </Text>
            <span style={VISUALLY_HIDDEN}>{t(`${OVERVIEW}.compare.notIncluded`)}</span>
          </>
        )}
      </Table.Cell>
      <Table.Cell style={{ background: 'var(--accent-a2)' }}>
        <Flex align="center" gap="1">
          <Icon name="check_circle" />
          {/* A phone keeps only the check, so the feature column has room to read. */}
          <Text
            size="2"
            weight="medium"
            style={compact ? VISUALLY_HIDDEN : { color: 'var(--accent-11)' }}
          >
            {t(`${OVERVIEW}.compare.included`)}
          </Text>
        </Flex>
      </Table.Cell>
    </Table.Row>
  );
}

function PlanColumnHeader({
  name,
  note,
  emphasis,
  compact,
}: {
  name: string;
  note: string;
  emphasis?: boolean;
  compact: boolean;
}) {
  return (
    <Table.ColumnHeaderCell
      style={{ width: compact ? undefined : '24%', background: emphasis ? 'var(--accent-a2)' : undefined }}
    >
      <Flex direction="column" gap="1">
        <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
          {name}
        </Text>
        {!compact && (
          <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 'normal' }}>
            {note}
          </Text>
        )}
      </Flex>
    </Table.ColumnHeaderCell>
  );
}

function CompareEditions() {
  const { t } = useTranslation();
  const compact = useIsMobile();
  const title = t(`${OVERVIEW}.compare.title`);
  return (
    <Flex asChild direction="column" gap="3">
      <section aria-labelledby={`${COMPARE_ID}-title`} id={COMPARE_ID} style={{ scrollMarginTop: 'var(--space-4)' }}>
        <Heading as="h2" id={`${COMPARE_ID}-title`} size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {title}
        </Heading>
        <Table.Root variant="surface" size="2">
          <caption style={VISUALLY_HIDDEN}>{title}</caption>
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>{t(`${OVERVIEW}.compare.feature`)}</Table.ColumnHeaderCell>
              <PlanColumnHeader
                name={t(`${OVERVIEW}.compare.community`)}
                note={t(`${OVERVIEW}.plans.community.yourPlan`)}
                compact={compact}
              />
              <PlanColumnHeader
                name={t(`${OVERVIEW}.compare.enterprise`)}
                note={t(`${OVERVIEW}.compare.pricing`)}
                emphasis
                compact={compact}
              />
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {ENTERPRISE_OVERVIEW_GROUPS.flatMap((group) => [
              <Table.Row key={group.key} style={{ background: 'var(--olive-2)' }}>
                <Table.ColumnHeaderCell colSpan={3} scope="colgroup">
                  <Text
                    size="1"
                    weight="bold"
                    style={{ color: 'var(--slate-11)', textTransform: 'uppercase', letterSpacing: '0.06em' }}
                  >
                    {t(`${OVERVIEW}.groups.${group.key}`)}
                  </Text>
                </Table.ColumnHeaderCell>
              </Table.Row>,
              ...group.items.map((item) => <CompareRow key={item.key} item={item} compact={compact} />),
            ])}
          </Table.Body>
        </Table.Root>
      </section>
    </Flex>
  );
}

const QUESTIONS = ['free', 'preview', 'get'] as const;

function Questions() {
  const { t } = useTranslation();
  const titleId = 'enterprise-questions';
  return (
    <Flex asChild direction="column" gap="3">
      <section aria-labelledby={titleId}>
        <Heading as="h2" id={titleId} size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t(`${OVERVIEW}.faq.title`)}
        </Heading>
        <Box
          style={{
            border: '1px solid var(--olive-4)',
            borderRadius: 'var(--radius-4)',
            background: 'var(--color-panel-solid)',
          }}
        >
          {QUESTIONS.map((key, i) => (
            <details
              key={key}
              data-testid="enterprise-question"
              style={{ borderTop: i === 0 ? undefined : '1px solid var(--olive-3)' }}
            >
              <summary
                className="touch-hit"
                style={{ cursor: 'pointer', padding: 'var(--space-3) var(--space-4)', color: 'var(--slate-12)' }}
              >
                <Text size="2" weight="medium">
                  {t(`${OVERVIEW}.faq.${key}.question`)}
                </Text>
              </summary>
              <Box style={{ padding: '0 var(--space-4) var(--space-3)' }}>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {/* Answers name the page's own link and button, so they read the same in every language. */}
                  {t(`${OVERVIEW}.faq.${key}.answer`, {
                    label: t(`${OVERVIEW}.seePreview`),
                    contact: t('workspace.enterprise.contact'),
                  })}
                </Text>
              </Box>
            </details>
          ))}
        </Box>
      </section>
    </Flex>
  );
}

/**
 * What the Enterprise Edition adds, for admins deciding whether they need it: the Community Edition
 * beside Enterprise, then every feature compared, then the questions admins ask first.
 */
export function EnterpriseOverviewPage() {
  const { t } = useTranslation();
  const isAdmin = useEnterpriseAdminGate();

  if (!isAdmin) {
    return null;
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer maxWidth={1080}>
        <Flex direction="column" gap="2" mb="5" style={{ maxWidth: 640 }}>
          <Badge size="1" variant="soft" radius="full" style={{ alignSelf: 'flex-start' }}>
            {t(`${OVERVIEW}.currentEdition`)}
          </Badge>
          <Heading as="h1" size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t(`${OVERVIEW}.title`)}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(`${OVERVIEW}.subtitle`)}
          </Text>
        </Flex>

        <Flex direction="column" gap="7">
          <Grid columns={{ initial: '1', md: '1fr 1.25fr' }} gap="4" align="stretch">
            <CommunityPlan />
            <EnterprisePlan />
          </Grid>
          <CompareEditions />
          <Questions />
        </Flex>
      </SettingsPageContainer>
    </Box>
  );
}
