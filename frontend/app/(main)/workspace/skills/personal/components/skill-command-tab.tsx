'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Flex, IconButton, RadioGroup, Switch, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SettingsRow } from '../../../components';
import {
  ARGUMENT_NAME_PATTERN,
  RESERVED_COMMAND_NAMES,
  renderCommand,
  type CommandArgument,
} from '@/lib/commands/render';
import { useCommandCatalog, useComposerPrefill } from '@/chat/commands';
import { commandProblemText } from '@/chat/components/chat-panel/slash-command-menu';
import type { CommandShare, CommandShareTargets, Skill } from '../types';

/**
 * What keeps a command from being saved, or null (SLASH1): the server refuses the same. A saved command
 * under a name a built-in took since is `shadowed`: the built-in runs instead, nothing renames it (SLASH2 O2).
 */
export function commandProblem(
  skill: Skill,
  name: string,
  existing = false,
): 'reserved' | 'shadowed' | 'argumentName' | 'duplicate' | null {
  if (!skill.invocation.user) return null;
  if (RESERVED_COMMAND_NAMES.has(name)) return existing ? 'shadowed' : 'reserved';
  if (skill.arguments.some((a) => !ARGUMENT_NAME_PATTERN.test(a.name))) return 'argumentName';
  if (new Set(skill.arguments.map((a) => a.name)).size !== skill.arguments.length) return 'duplicate';
  return null;
}

const splitValues = (text: string): string[] | undefined => {
  const values = text.split(',').map((v) => v.trim()).filter(Boolean);
  return values.length > 0 ? values : undefined;
};

function ArgumentRow({
  arg,
  readOnly,
  onChange,
  onRemove,
}: {
  arg: CommandArgument;
  readOnly: boolean;
  onChange: (next: CommandArgument) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex gap="2" align="center" wrap="wrap" data-testid="command-argument-row">
      <TextField.Root
        size="1"
        style={{ width: 120 }}
        aria-label={t('workspace.skills.command.argName')}
        placeholder={t('workspace.skills.command.argName')}
        value={arg.name}
        disabled={readOnly}
        onChange={(e) => onChange({ ...arg, name: e.target.value.trim() })}
      />
      <TextField.Root
        size="1"
        style={{ flex: 1, minWidth: 140 }}
        aria-label={t('workspace.skills.command.argDescription')}
        placeholder={t('workspace.skills.command.argDescription')}
        value={arg.description ?? ''}
        disabled={readOnly}
        onChange={(e) => onChange({ ...arg, description: e.target.value || undefined })}
      />
      <TextField.Root
        size="1"
        style={{ width: 130 }}
        aria-label={t('workspace.skills.command.argValues')}
        placeholder={t('workspace.skills.command.argValues')}
        value={(arg.enum ?? []).join(', ')}
        disabled={readOnly}
        onChange={(e) => onChange({ ...arg, enum: splitValues(e.target.value) })}
      />
      <TextField.Root
        size="1"
        style={{ width: 100 }}
        aria-label={t('workspace.skills.command.argDefault')}
        placeholder={t('workspace.skills.command.argDefault')}
        value={arg.default ?? ''}
        disabled={readOnly}
        onChange={(e) => onChange({ ...arg, default: e.target.value || undefined })}
      />
      <Text as="label" size="1" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <Switch
          size="1"
          checked={arg.required === true}
          disabled={readOnly}
          onCheckedChange={(required) => onChange({ ...arg, required: required || undefined })}
        />
        {t('workspace.skills.command.argRequired')}
      </Text>
      {!readOnly ? (
        <IconButton size="1" variant="ghost" color="gray" aria-label={t('workspace.skills.command.removeArgument')} onClick={onRemove}>
          <MaterialIcon name="close" size={14} />
        </IconButton>
      ) : null}
    </Flex>
  );
}

const shareValue = (share: CommandShare): string =>
  share.scope === 'team' ? `team:${share.teamId ?? ''}` : share.scope;

const shareFromValue = (value: string): CommandShare =>
  value.startsWith('team:') ? { scope: 'team', teamId: value.slice(5) } : { scope: value as 'personal' | 'org', teamId: null };

/**
 * "Who can use it" (SLASH1 S4): only the author, one of the teams the caller owns, or the org for its admin.
 * Shown only when there is somewhere to share to, or the command is already shared.
 */
export function CommandShareChoice({
  share,
  onChange,
  targets,
  currentTeam,
  disabled,
}: {
  share: CommandShare;
  onChange: (next: CommandShare) => void;
  targets: CommandShareTargets | null;
  /** The name of the team it is shared with now, which the caller may not own (an org admin's edit). */
  currentTeam: string | null;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const teams = [...(targets?.teams ?? [])];
  if (share.scope === 'team' && share.teamId && !teams.some((team) => team.id === share.teamId)) {
    teams.push({ id: share.teamId, name: currentTeam ?? share.teamId });
  }
  const orgOption = targets?.org === true || share.scope === 'org';
  if (teams.length === 0 && !orgOption) return null;
  const option = (value: string, label: string) => (
    <Text as="label" size="2" key={value}>
      <Flex align="center" gap="2" style={{ cursor: disabled ? 'default' : 'pointer' }}>
        <RadioGroup.Item value={value} />
        {label}
      </Flex>
    </Text>
  );
  return (
    <SettingsRow label={t('workspace.skills.command.share')} description={t('workspace.skills.command.shareDescription')}>
      <RadioGroup.Root
        value={shareValue(share)}
        onValueChange={(value) => onChange(shareFromValue(value))}
        disabled={disabled}
        aria-label={t('workspace.skills.command.share')}
        data-testid="skill-command-share"
      >
        <Flex direction="column" gap="2">
          {option('personal', t('workspace.skills.command.sharePersonal'))}
          {teams.map((team) => option(`team:${team.id}`, t('workspace.skills.command.shareTeam', { team: team.name })))}
          {orgOption ? option('org', t('workspace.skills.command.shareOrg')) : null}
        </Flex>
      </RadioGroup.Root>
    </SettingsRow>
  );
}

/**
 * The skills editor's "/command" tab (SLASH1 D6): whether people can run the
 * skill from the chat composer, its arguments, a preview with sample
 * arguments, and "Try it" in a new chat.
 */
export function SkillCommandTab({
  skill,
  name,
  setSkill,
  readOnly,
  isCreate,
  saved,
  onTry,
  share,
  onShareChange,
  shareTargets = null,
}: {
  skill: Skill;
  name: string;
  setSkill: React.Dispatch<React.SetStateAction<Skill>>;
  readOnly: boolean;
  isCreate: boolean;
  /** The command exists on the server as shown: "Try it" sends what was saved. */
  saved: boolean;
  onTry: () => void;
  /** Who it is shared with, as the editor will save it; absent where sharing is not offered. */
  share?: CommandShare;
  onShareChange?: (next: CommandShare) => void;
  shareTargets?: CommandShareTargets | null;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const [sample, setSample] = useState('');
  const enabled = skill.invocation.user;
  const preview = useMemo(() => renderCommand(skill.body, skill.arguments, sample), [skill.body, skill.arguments, sample]);
  const problem = commandProblem(skill, name, !isCreate);
  const previewProblem = commandProblemText(t, preview);

  const setArgs = (next: CommandArgument[]) => setSkill((s) => ({ ...s, arguments: next }));

  const tryIt = () => {
    useCommandCatalog.getState().invalidate();
    useComposerPrefill.getState().request(`/${name} `);
    onTry();
    router.push('/chat');
  };

  return (
    <Flex direction="column" gap="4" data-testid="skill-command-tab">
      <SettingsRow
        label={t('workspace.skills.command.toggle')}
        description={t('workspace.skills.command.toggleDescription')}
      >
        <Switch
          checked={enabled}
          disabled={readOnly}
          aria-label={t('workspace.skills.command.toggle')}
          onCheckedChange={(user) =>
            setSkill((s) => ({
              ...s,
              // A new command stays out of the agent's own catalog unless the author says otherwise (D1).
              invocation: { user, model: user && isCreate ? false : s.invocation.model },
            }))
          }
        />
      </SettingsRow>

      {enabled ? (
        <>
          <SettingsRow
            label={t('workspace.skills.command.modelToggle')}
            description={t('workspace.skills.command.modelToggleDescription')}
          >
            <Switch
              checked={skill.invocation.model}
              disabled={readOnly}
              aria-label={t('workspace.skills.command.modelToggle')}
              onCheckedChange={(model) => setSkill((s) => ({ ...s, invocation: { ...s.invocation, model } }))}
            />
          </SettingsRow>

          {share && onShareChange ? (
            <CommandShareChoice
              share={share}
              onChange={onShareChange}
              targets={shareTargets}
              currentTeam={skill.team ?? null}
              disabled={readOnly}
            />
          ) : null}

          <Flex direction="column" gap="2">
            <Text size="2" weight="medium">{t('workspace.skills.command.arguments')}</Text>
            <Text size="1" color="gray">{t('workspace.skills.command.argumentsHint')}</Text>
            {skill.arguments.map((arg, index) => (
              <ArgumentRow
                key={index}
                arg={arg}
                readOnly={readOnly}
                onChange={(next) => setArgs(skill.arguments.map((a, i) => (i === index ? next : a)))}
                onRemove={() => setArgs(skill.arguments.filter((_, i) => i !== index))}
              />
            ))}
            {!readOnly ? (
              <Box>
                <Button
                  size="1"
                  variant="soft"
                  color="gray"
                  onClick={() => setArgs([...skill.arguments, { name: '' }])}
                  disabled={skill.arguments.length >= 20}
                >
                  <MaterialIcon name="add" size={14} />
                  {t('workspace.skills.command.addArgument')}
                </Button>
              </Box>
            ) : null}
          </Flex>

          {problem ? (
            <Text size="1" color="red" role="alert" data-testid="skill-command-problem">
              {problem === 'reserved'
                ? t('workspace.skills.command.problemReserved', { name })
                : problem === 'shadowed'
                  ? t('workspace.skills.command.problemShadowed', { name })
                  : problem === 'argumentName'
                    ? t('workspace.skills.command.problemArgumentName')
                    : t('workspace.skills.command.problemDuplicate')}
            </Text>
          ) : null}

          <Flex direction="column" gap="2">
            <Text size="2" weight="medium">{t('workspace.skills.command.preview')}</Text>
            <TextField.Root
              size="2"
              value={sample}
              aria-label={t('workspace.skills.command.sampleArguments')}
              placeholder={t('workspace.skills.command.sampleArguments')}
              onChange={(e) => setSample(e.target.value)}
            >
              <TextField.Slot>
                <Text size="1" style={{ fontFamily: 'monospace' }}>/{name || '…'}</Text>
              </TextField.Slot>
            </TextField.Root>
            <Box
              data-testid="skill-command-preview"
              aria-live="polite"
              style={{
                padding: 'var(--space-2)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--slate-2)',
                border: '1px solid var(--slate-a4)',
                maxHeight: 200,
                overflowY: 'auto',
              }}
            >
              <Text size="2" color={previewProblem ? 'red' : undefined} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {previewProblem ?? ('text' in preview ? preview.text : '')}
              </Text>
            </Box>
          </Flex>

          <Flex align="center" gap="2">
            <Button size="2" variant="soft" onClick={tryIt} disabled={!saved || problem !== null} data-testid="skill-command-try">
              <MaterialIcon name="play_arrow" size={16} />
              {t('workspace.skills.command.tryIt')}
            </Button>
            {!saved ? <Text size="1" color="gray">{t('workspace.skills.command.saveFirst')}</Text> : null}
          </Flex>
        </>
      ) : null}
    </Flex>
  );
}

const SHADOWED_SEEN_KEY = 'pipeshub.slash2.shadowedCommandsSeen';

/** The commands of `skills` a built-in now hides (SLASH2 O2): usable as a /command under a reserved name. */
export function shadowedCommands(skills: ReadonlyArray<Pick<Skill, 'name' | 'invocation'>>): string[] {
  return skills.filter((s) => s.invocation?.user && RESERVED_COMMAND_NAMES.has(s.name)).map((s) => s.name).sort();
}

/**
 * The one-time list of commands a built-in hides (O2), above the skills an editor may change: an org admin's
 * includes the org's commands. Dismissed per browser for this exact list; a new collision shows it again.
 */
export function ShadowedCommandsNotice({ skills }: { skills: ReadonlyArray<Pick<Skill, 'name' | 'invocation'>> }) {
  const { t } = useTranslation();
  const names = shadowedCommands(skills);
  const listKey = names.join(',');
  const [seen, setSeen] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(SHADOWED_SEEN_KEY);
    } catch {
      return null;
    }
  });
  if (names.length === 0 || seen === listKey) return null;
  const dismiss = () => {
    setSeen(listKey);
    try {
      window.localStorage.setItem(SHADOWED_SEEN_KEY, listKey);
    } catch {
      // Without storage it shows again next visit; nothing is lost.
    }
  };
  return (
    <Callout.Root color="amber" size="1" role="status" data-testid="shadowed-commands-notice" style={{ margin: 'var(--space-3) var(--space-4) 0' }}>
      <Callout.Icon>
        <MaterialIcon name="info" size={16} />
      </Callout.Icon>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium">{t('workspace.skills.command.shadowedTitle')}</Text>
        <Text size="1">{t('workspace.skills.command.shadowedBody')}</Text>
        <Text size="1" style={{ fontFamily: 'monospace' }}>
          {names.map((n) => `/${n}`).join('  ')}
        </Text>
        <Box>
          <Button size="1" variant="soft" color="gray" onClick={dismiss}>
            {t('workspace.skills.command.shadowedDismiss')}
          </Button>
        </Box>
      </Flex>
    </Callout.Root>
  );
}
