'use client';

import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SkillFinding } from '../types';

/** The scanner's codes with words of their own; any other reads as "needs a closer look". */
const FINDING_CODES = new Set([
  'remote_code_execution',
  'encoded_code_execution',
  'credential_access',
  'destructive_command',
  'native_binary',
  'instruction_override',
  'hidden_directive',
  'secret_access',
  'exfiltration',
  'concealment',
  'tool_shadowing',
  'invisible_text',
]);

export function findingLabel(t: (key: string) => string, code: string): string {
  return t(`workspace.skills.scan.finding.${FINDING_CODES.has(code) ? code : 'other'}`);
}

/** What the content scanner found, with the file and line each finding is on. */
export function SkillFindings({ findings }: { findings: SkillFinding[] }) {
  const { t } = useTranslation();
  if (findings.length === 0) return null;
  return (
    <Flex direction="column" gap="1" data-testid="skill-findings">
      <Text size="1" weight="medium" style={{ color: 'var(--amber-11)' }}>
        {t('workspace.skills.scan.findingsTitle')}
      </Text>
      <Text size="1" style={{ color: 'var(--gray-11)' }}>{t('workspace.skills.scan.findingsHint')}</Text>
      {findings.map((finding, index) => (
        <Flex key={`${finding.path}:${finding.line}:${finding.code}:${index}`} direction="column" gap="0">
          <Flex align="center" gap="1">
            <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
            <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>{findingLabel(t, finding.code)}</Text>
          </Flex>
          <Text size="1" style={{ color: 'var(--gray-11)', paddingLeft: 'var(--space-4)' }}>
            {finding.line > 0
              ? t('workspace.skills.scan.at', { path: finding.path, line: finding.line })
              : finding.path}
            {finding.excerpt ? ` · ${finding.excerpt}` : ''}
          </Text>
        </Flex>
      ))}
    </Flex>
  );
}
