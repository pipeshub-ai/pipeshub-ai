'use client';

import { Flex, Text } from '@radix-ui/themes';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import type { PushRefusal, RepoActivity } from '@/chat/repository-steps';

const REFUSAL_KEYS: Record<PushRefusal, string> = {
  otherBranch: 'agentActivity.repo.refused.otherBranch',
  overwrite: 'agentActivity.repo.refused.overwrite',
  delete: 'agentActivity.repo.refused.delete',
  tags: 'agentActivity.repo.refused.tags',
  other: 'agentActivity.repo.refused.other',
};

/** The row's words, one line: no command output, no ids, no JSON. */
export function repoActivityText(t: TFunction, repo: RepoActivity): string {
  switch (repo.action) {
    case 'copied':
      return repo.branch
        ? t('agentActivity.repo.copied', { repository: repo.repository, branch: repo.branch })
        : t('agentActivity.repo.copiedNoBranch', { repository: repo.repository });
    case 'pushed':
      return repo.branch ? t('agentActivity.repo.pushed', { branch: repo.branch }) : t('agentActivity.repo.pushedNoBranch');
    case 'pushRefused': {
      const head = repo.target
        ? t('agentActivity.repo.pushFailed', { target: repo.target })
        : t('agentActivity.repo.pushFailedNoTarget');
      return `${head} · ${t(REFUSAL_KEYS[repo.reason])}`;
    }
    case 'prOpened':
      if (repo.number !== null && repo.repository) {
        return t('agentActivity.repo.prOpened', { number: repo.number, repository: repo.repository });
      }
      return t('agentActivity.repo.prOpenedPlain');
    default:
      return t('agentActivity.repo.prFailed');
  }
}

/** Only a link to a pull request on github.com is offered. */
const safeGithubUrl = (url: string | null): string | null =>
  url && /^https:\/\/github\.com\/[^\s]+$/.test(url) ? url : null;

/** One repository step of an engine run (WP-26): copied, pushed, a refused push, an opened pull request. */
export function EngineRepoRow({ repo }: { repo: RepoActivity }) {
  const { t } = useTranslation();
  const failed = repo.action === 'pushRefused' || repo.action === 'prFailed';
  const link = repo.action === 'prOpened' ? safeGithubUrl(repo.url) : null;
  return (
    <Flex align="center" gap="2" wrap="wrap" data-testid="engine-repo" data-action={repo.action} style={{ minWidth: 0, minHeight: 24 }}>
      <Text size="1" style={{ color: failed ? 'var(--red-11)' : 'var(--slate-11)', overflowWrap: 'anywhere', minWidth: 0 }}>
        {repoActivityText(t, repo)}
      </Text>
      {link ? (
        <Text size="1" asChild>
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="engine-repo-pr-link"
            style={{ color: 'var(--accent-11)', display: 'inline-flex', alignItems: 'center', minHeight: 24 }}
          >
            {t('agentActivity.repo.viewOnGithub')}
          </a>
        </Text>
      ) : null}
    </Flex>
  );
}
