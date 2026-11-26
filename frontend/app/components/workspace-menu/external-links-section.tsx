'use client';

import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { useTranslation } from 'react-i18next';
import { MenuItem } from './menu-item';
import { useGitHubStars } from './hooks/use-github-stars';
import { useBrand } from '@/lib/branding';
import { useFeedbackDialogStore } from '@/app/(main)/feedback/store';
import { useSupportDialogStore } from '@/app/(main)/support/store';

interface ExternalLinksSectionProps {
  /** Closes the menu before a dialog opens over it. */
  onDialogOpen: () => void;
}

/**
 * Help section of the workspace menu:
 *   Documentation, Contact support, Send feedback, GitHub (with live star count)
 */
export function ExternalLinksSection({ onDialogOpen }: ExternalLinksSectionProps) {
  const stars = useGitHubStars();
  const { t } = useTranslation();
  const brand = useBrand();
  const docsUrl = brand.docsHref() ?? EXTERNAL_LINKS.documentation;
  const githubUrl = brand.githubUrl;
  const openSupport = useSupportDialogStore((s) => s.open);
  const openFeedback = useFeedbackDialogStore((s) => s.open);

  return (
    <Flex direction="column" gap="1">
      {docsUrl ? (
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
        href={docsUrl}
      />
      ) : null}

      <MenuItem
        icon={
          <MaterialIcon
            name="support_agent"
            size={ICON_SIZE_DEFAULT}
            color="var(--slate-11)"
          />
        }
        label={t('support.menuLabel')}
        onClick={() => {
          onDialogOpen();
          openSupport();
        }}
      />

      <MenuItem
        icon={
          <MaterialIcon
            name="feedback"
            size={ICON_SIZE_DEFAULT}
            color="var(--slate-11)"
          />
        }
        label={t('feedback.menuLabel')}
        onClick={() => {
          onDialogOpen();
          openFeedback();
        }}
      />

      {githubUrl ? (
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
                <Text size="2" style={{ color: 'var(--slate-11)' }}>•</Text>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 2,
                    fontSize: 14,
                    fontWeight: 500,
                    lineHeight: '20px',
                    color: 'var(--slate-11)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {stars}
                  <MaterialIcon
                    name="star"
                    size={12}
                    color="var(--slate-11)"
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
        href={githubUrl}
      />
      ) : null}
    </Flex>
  );
}
