'use client';

import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { useTranslation } from 'react-i18next';
import { MenuItem } from './menu-item';
import { useGitHubStars } from './hooks/use-github-stars';

/**
 * External links section of the workspace menu:
 *   Documentation, GitHub (with live star count)
 */
export function ExternalLinksSection() {
  const stars = useGitHubStars();
  const { t } = useTranslation();

  return (
    <Flex direction="column" gap="1">
      <MenuItem
        icon={
          <img
            src="/icons/common/reader.svg"
            width={ICON_SIZE_DEFAULT}
            height={ICON_SIZE_DEFAULT}
            alt=""
            style={{ flexShrink: 0, opacity: 0.7 }}
          />
        }
        label={t('workspaceMenu.documentation')}
        rightSlot={
          <MaterialIcon
            name="open_in_new"
            size={ICON_SIZE_DEFAULT}
            color="var(--slate-11)"
          />
        }
        href={EXTERNAL_LINKS.documentation}
      />

      <MenuItem
        icon={
          <img
            src="/icons/logos/github-logo.svg"
            width={ICON_SIZE_DEFAULT}
            height={ICON_SIZE_DEFAULT}
            alt=""
            style={{ flexShrink: 0, opacity: 0.7 }}
          />
        }
        label={
          <Flex align="center" gap="2" style={{ overflow: 'hidden' }}>
            <span
              style={{
                whiteSpace: 'nowrap',
                fontSize: 14,
                fontWeight: 400,
                lineHeight: '20px',
                color: 'var(--slate-11)',
              }}
            >
              GitHub
            </span>
            {stars && (
              <>
                <Text style={{ color: 'var(--slate-6)' }}>•</Text>
                <span
                  style={{
                    fontSize: 14,
                    fontWeight: 500,
                    lineHeight: '20px',
                    color: 'var(--slate-10)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {stars}
                  <MaterialIcon
                    name="star"
                    size={12}
                    color="var(--slate-10)"
                    style={{ marginLeft: 2, verticalAlign: 'middle' }}
                  />
                </span>
              </>
            )}
          </Flex>
        }
        rightSlot={
          <MaterialIcon
            name="open_in_new"
            size={ICON_SIZE_DEFAULT}
            color="var(--slate-11)"
          />
        }
        href={EXTERNAL_LINKS.github}
      />
    </Flex>
  );
}
