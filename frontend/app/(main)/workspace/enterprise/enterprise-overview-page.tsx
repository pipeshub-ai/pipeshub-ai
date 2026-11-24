'use client';

import { Link } from '@/lib/navigation';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Grid, Heading, Link as RadixLink, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { enterpriseContactUrl } from '@/lib/constants/enterprise';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { useEnterpriseAdminGate } from '@/app/(main)/workspace/components/use-enterprise-admin-gate';
import { ENTERPRISE_FEATURES } from './enterprise-feature-pages';
import { ENTERPRISE_OVERVIEW_GROUPS, type EnterpriseOverviewItem } from './enterprise-overview';

const OVERVIEW = 'workspace.enterprise.overview';

function FeatureCard({ item }: { item: EnterpriseOverviewItem }) {
  const { t } = useTranslation();
  const name = t(`${OVERVIEW}.items.${item.key}.name`);

  return (
    <Flex
      direction="column"
      gap="2"
      data-testid="enterprise-overview-item"
      style={{
        padding: 'var(--space-4)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--olive-2)',
      }}
    >
      <Flex align="center" gap="2">
        <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
          <MaterialIcon name={item.icon} size={18} color="var(--accent-11)" />
        </span>
        <Heading as="h3" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {name}
        </Heading>
      </Flex>
      <Text size="2" style={{ color: 'var(--slate-11)', flex: 1 }}>
        {t(`${OVERVIEW}.items.${item.key}.description`)}
      </Text>
      {item.feature && (
        <RadixLink asChild size="2" weight="medium" style={{ alignSelf: 'flex-start' }}>
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
  );
}

/** Everything in the Enterprise Edition on one page, for admins deciding whether they need it. */
export function EnterpriseOverviewPage() {
  const { t } = useTranslation();
  const isAdmin = useEnterpriseAdminGate();

  if (!isAdmin) {
    return null;
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer maxWidth={1080}>
        <Flex
          direction={{ initial: 'column', sm: 'row' }}
          align={{ initial: 'start', sm: 'end' }}
          justify="between"
          gap="4"
          mb="6"
        >
          <Flex direction="column" gap="2" style={{ minWidth: 0, maxWidth: 640 }}>
            <Badge size="1" variant="soft" radius="full" style={{ alignSelf: 'flex-start' }}>
              {t(`${OVERVIEW}.currentEdition`)}
            </Badge>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t(`${OVERVIEW}.title`)}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t(`${OVERVIEW}.subtitle`)}
            </Text>
          </Flex>
          <Button asChild size="2" className="touch-hit" style={{ flexShrink: 0 }}>
            <a href={enterpriseContactUrl('overview')} target="_blank" rel="noopener noreferrer">
              {/* The icon is a text ligature; hide it so the link is named by its label alone. */}
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon name="event" size={16} color="currentColor" />
              </span>
              {t('workspace.enterprise.contact')}
            </a>
          </Button>
        </Flex>

        <Flex direction="column" gap="6">
          {ENTERPRISE_OVERVIEW_GROUPS.map((group) => (
            <Flex asChild key={group.key} direction="column" gap="3">
              <section aria-labelledby={`enterprise-group-${group.key}`}>
                <Heading
                  as="h2"
                  id={`enterprise-group-${group.key}`}
                  size="2"
                  weight="medium"
                  style={{ color: 'var(--slate-11)' }}
                >
                  {t(`${OVERVIEW}.groups.${group.key}`)}
                </Heading>
                <Grid columns={{ initial: '1', sm: '2', lg: '3' }} gap="3">
                  {group.items.map((item) => (
                    <FeatureCard key={item.key} item={item} />
                  ))}
                </Grid>
              </section>
            </Flex>
          ))}
        </Flex>

        <Flex
          align="start"
          gap="3"
          mt="6"
          style={{ padding: 'var(--space-4)', borderRadius: 'var(--radius-3)', background: 'var(--olive-2)' }}
        >
          <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
            <MaterialIcon name="verified_user" size={18} color="var(--slate-11)" />
          </span>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(`${OVERVIEW}.communityNote`)}
          </Text>
        </Flex>
      </SettingsPageContainer>
    </Box>
  );
}
