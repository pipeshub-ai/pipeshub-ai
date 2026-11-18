'use client';

import React, { useEffect, useState } from 'react';
import { Badge, Button, Code, Dialog, Flex, Link, Table, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import summary from '../acme-corp-summary.json';
import { findSampleAccounts } from '../remove-demo-data';
import { useDemoDataStatus } from '../use-demo-data';
import { useDemoSwitch } from '../use-demo-switch';

type AccountState = 'checking' | 'active' | 'off' | 'missing' | 'unknown';

const SIGN_IN_PEOPLE = summary.people.filter((p) => p.signIn);

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Flex direction="column" gap="2" style={{ minWidth: 0 }}>
      <Text as="div" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>{title}</Text>
      {children}
    </Flex>
  );
}

/** Whether the demo reaches this person's answers right now, and the way to show it if not. */
function StatusLine() {
  const { t } = useTranslation();
  const status = useDemoDataStatus();
  const { setInclude, busy } = useDemoSwitch();
  if (!status?.hasDemo) return null;

  let text = t('demoData.about.status.shown');
  if (status.offForEveryone) text = t('demoData.about.status.offForEveryone');
  else if (!status.include) {
    text = status.chosen === false ? t('demoData.about.status.hiddenByChoice') : t('demoData.about.status.hiddenRealData');
  }
  const canShow = !status.offForEveryone && !status.include;

  return (
    <Flex
      direction="column"
      gap="2"
      p="3"
      style={{ background: 'var(--gray-a2)', border: '1px solid var(--gray-a4)', borderRadius: 'var(--radius-3)' }}
    >
      <Text size="2">{text}</Text>
      {canShow && (
        <Flex>
          <Button size="1" variant="soft" disabled={busy} onClick={() => void setInclude(true)}>
            {t('demoData.about.status.show')}
          </Button>
        </Flex>
      )}
    </Flex>
  );
}

/** Admins only: which sample accounts exist here, and how they sign in. */
function SignInAccounts({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const status = useDemoDataStatus();
  // null while looking, 'failed' if the lookup failed, else the addresses found.
  const [existing, setExisting] = useState<Set<string> | 'failed' | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setExisting(null);
    // Every sample account counts here, including one the viewer is signed in as.
    findSampleAccounts()
      .then((found) => {
        if (!cancelled) setExisting(new Set(found.map((a) => a.email.trim().toLowerCase())));
      })
      .catch(() => {
        if (!cancelled) setExisting('failed');
      });
    return () => {
      cancelled = true;
    };
  }, [open, attempt]);

  const stateOf = (email: string): AccountState => {
    if (existing === 'failed') return 'unknown';
    if (existing === null || status === null) return 'checking';
    if (!existing.has(email)) return 'missing';
    return status.offForEveryone ? 'off' : 'active';
  };
  const states = SIGN_IN_PEOPLE.map((p) => stateOf(p.email as string));
  const found = SIGN_IN_PEOPLE.filter((_, i) => states[i] === 'active' || states[i] === 'off');
  const color: Record<AccountState, 'green' | 'gray' | 'orange' | 'red'> = {
    checking: 'gray', active: 'green', off: 'gray', missing: 'orange', unknown: 'red',
  };

  return (
    <Section title={t('demoData.about.accounts.title')}>
      <Text size="2" color="gray">{t('demoData.about.accounts.description')}</Text>
      <Flex direction="column" gap="2">
        {SIGN_IN_PEOPLE.map((p, i) => (
          <Flex key={p.email} align="center" justify="between" gap="3" wrap="wrap">
            <Text size="2">
              {p.name} <Text color="gray">({p.email})</Text>
            </Text>
            <Badge size="1" variant="soft" color={color[states[i]]}>
              {t(`demoData.about.accounts.state.${states[i]}`)}
            </Badge>
          </Flex>
        ))}
      </Flex>
      {existing === 'failed' && (
        <Flex>
          <Button size="1" variant="soft" color="gray" onClick={() => setAttempt((n) => n + 1)}>
            {t('demoData.about.accounts.retry')}
          </Button>
        </Flex>
      )}
      {found.length > 0 && (
        <>
          <Text size="2" color="gray">{t('demoData.about.accounts.password')}</Text>
          <Text size="2" color="gray">{t('demoData.about.accounts.lostPassword')}</Text>
          <Code size="1" style={{ display: 'block', padding: 'var(--space-2)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {found.map((p) => `cd /app/backend && node dist/scripts/set-password-link.js ${p.email}`).join('\n')}
          </Code>
        </>
      )}
      {states.includes('missing') && <Text size="2" color="gray">{t('demoData.about.accounts.missing')}</Text>}
    </Section>
  );
}

interface AboutDemoDataDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isAdmin: boolean | null;
}

/**
 * What the Acme Corp demo is: its tools and records, its people, what only
 * some of them can see, and the questions it answers. Built from a summary of
 * the fixture that test_demo_fixture.py keeps in step.
 */
export function AboutDemoDataDialog({ open, onOpenChange, isAdmin }: AboutDemoDataDialogProps) {
  const { t } = useTranslation();
  const questions = Object.values(
    t('chat.demoSuggestions', { returnObjects: true }) as Record<string, { text: string }>,
  ).map((q) => q.text);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content style={{ maxWidth: '40rem' }}>
        <Dialog.Title>{t('demoData.about.title')}</Dialog.Title>
        <Dialog.Description size="2" color="gray">{t('demoData.about.description')}</Dialog.Description>

        <Flex direction="column" gap="5" mt="4">
          <StatusLine />

          <Section title={t('demoData.about.tools.title')}>
            <div style={{ overflowX: 'auto' }}>
              <Table.Root size="1" variant="surface">
                <Table.Header>
                  <Table.Row>
                    <Table.ColumnHeaderCell>{t('demoData.about.tools.tool')}</Table.ColumnHeaderCell>
                    <Table.ColumnHeaderCell justify="end">{t('demoData.about.tools.places')}</Table.ColumnHeaderCell>
                    <Table.ColumnHeaderCell justify="end">{t('demoData.about.tools.records')}</Table.ColumnHeaderCell>
                  </Table.Row>
                </Table.Header>
                <Table.Body style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {summary.tools.map((tool) => (
                    <Table.Row key={tool.name}>
                      <Table.RowHeaderCell>{tool.name}</Table.RowHeaderCell>
                      <Table.Cell justify="end">{tool.places}</Table.Cell>
                      <Table.Cell justify="end">{tool.records}</Table.Cell>
                    </Table.Row>
                  ))}
                  <Table.Row>
                    <Table.RowHeaderCell><Text weight="medium">{t('demoData.about.tools.total')}</Text></Table.RowHeaderCell>
                    <Table.Cell />
                    <Table.Cell justify="end"><Text weight="medium">{summary.recordCount}</Text></Table.Cell>
                  </Table.Row>
                </Table.Body>
              </Table.Root>
            </div>
          </Section>

          <Section title={t('demoData.about.people.title')}>
            <Text size="2" color="gray">{t('demoData.about.people.description')}</Text>
            <Flex direction="column" gap="1" asChild>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {summary.people.map((p) => (
                  <li key={p.name}>
                    <Flex align="center" gap="2" wrap="wrap">
                      <Text size="2" weight="medium">{p.name}</Text>
                      <Text size="2" color="gray">{p.title}</Text>
                      {p.signIn && (
                        <Badge size="1" variant="soft" color="green">{t('demoData.about.people.canSignIn')}</Badge>
                      )}
                    </Flex>
                  </li>
                ))}
              </ul>
            </Flex>
          </Section>

          <Section title={t('demoData.about.restricted.title')}>
            <Text size="2" color="gray">{t('demoData.about.restricted.description')}</Text>
            <Flex direction="column" gap="3">
              {summary.restricted.map((r) => (
                <Flex key={r.group} direction="column" gap="1">
                  <Text size="2" weight="medium">{r.group}</Text>
                  {r.records.map((title) => (
                    <Text key={title} size="2" color="gray">{title}</Text>
                  ))}
                  <Text size="1" color="gray">
                    {r.signInReaders.length > 0
                      ? t('demoData.about.restricted.readers', { names: r.signInReaders.join(', ') })
                      : t('demoData.about.restricted.noReaders')}
                  </Text>
                </Flex>
              ))}
            </Flex>
          </Section>

          <Section title={t('demoData.about.questions.title')}>
            <Flex direction="column" gap="1" asChild>
              <ul style={{ margin: 0, paddingLeft: 'var(--space-5)' }}>
                {questions.map((q) => (
                  <li key={q}><Text size="2">{q}</Text></li>
                ))}
              </ul>
            </Flex>
          </Section>

          {isAdmin === true && <SignInAccounts open={open} />}
        </Flex>

        <Flex justify="end" mt="5">
          <Dialog.Close>
            <Button variant="soft" color="gray">{t('demoData.about.close')}</Button>
          </Dialog.Close>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

/** A "What's in the demo?" link that opens the panel; for running text and button rows alike. */
export function AboutDemoDataLink({ isAdmin, size = '1' }: { isAdmin: boolean | null; size?: '1' | '2' }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Link asChild size={size} weight="medium">
        <button
          type="button"
          className="touch-size"
          onClick={() => setOpen(true)}
          style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'center' }}
        >
          {t('demoData.about.open')}
        </button>
      </Link>
      <AboutDemoDataDialog open={open} onOpenChange={setOpen} isAdmin={isAdmin} />
    </>
  );
}
