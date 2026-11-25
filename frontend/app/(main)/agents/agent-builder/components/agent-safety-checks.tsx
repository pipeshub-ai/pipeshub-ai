'use client';

import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow } from '@xyflow/react';
import { Button, Flex, IconButton, Select, Switch, Text, TextArea, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  DEFAULT_COMMAND_TIMEOUT_S,
  FILE_FORMATS,
  FIX_ROUNDS,
  MAX_CHECKS,
  agentGatesOf,
  checkProblem,
  commandArgv,
  commandLine,
  type AgentGates,
  type FileFormat,
  type VerifyCheck,
} from '../../agent-gates';

type CheckKind = VerifyCheck['kind'];
const KINDS: CheckKind[] = ['file', 'json_schema', 'citations', 'command'];
const ANY_FORMAT = 'any';

/** One check as the author edits it: free text until it reads as a check. */
interface CheckRow {
  id: number;
  kind: CheckKind;
  path: string;
  format: FileFormat | typeof ANY_FORMAT;
  schema: string;
  command: string;
  timeoutS: string;
}

function rowOf(id: number, check: VerifyCheck): CheckRow {
  const row: CheckRow = { id, kind: check.kind, path: '', format: ANY_FORMAT, schema: '', command: '', timeoutS: '' };
  if (check.kind === 'file') return { ...row, path: check.path, format: check.format ?? ANY_FORMAT };
  if (check.kind === 'json_schema') return { ...row, path: check.path, schema: JSON.stringify(check.schema, null, 2) };
  if (check.kind === 'command') {
    return { ...row, command: commandLine(check.argv), timeoutS: check.timeoutS !== undefined ? String(check.timeoutS) : '' };
  }
  return row;
}

/** The row as a check, or why it is not one yet (a locale key). */
export function checkOf(row: CheckRow): { check: VerifyCheck } | { problem: string } {
  let check: VerifyCheck;
  switch (row.kind) {
    case 'file':
      check = { kind: 'file', path: row.path.trim(), ...(row.format !== ANY_FORMAT ? { format: row.format } : {}) };
      break;
    case 'json_schema': {
      let schema: unknown;
      try {
        schema = JSON.parse(row.schema);
      } catch {
        return { problem: 'agentBuilder.gates.problem.schema' };
      }
      if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) {
        return { problem: 'agentBuilder.gates.problem.schema' };
      }
      check = { kind: 'json_schema', path: row.path.trim(), schema: schema as Record<string, unknown> };
      break;
    }
    case 'command': {
      const seconds = row.timeoutS.trim() === '' ? DEFAULT_COMMAND_TIMEOUT_S : Number(row.timeoutS);
      if (!Number.isInteger(seconds) || seconds < 1 || seconds > 600) return { problem: 'agentBuilder.gates.problem.timeout' };
      check = { kind: 'command', argv: commandArgv(row.command), timeoutS: seconds };
      break;
    }
    default:
      check = { kind: 'citations' };
  }
  const problem = checkProblem(check);
  return problem ? { problem } : { check };
}

/** The gates the section's state saves: only checks that read as checks. */
function gatesOf(plan: boolean, verify: boolean, rows: CheckRow[], onFail: 'continue' | 'flag'): AgentGates {
  const checks = rows.flatMap((row) => {
    const read = checkOf(row);
    return 'check' in read ? [read.check] : [];
  });
  return {
    ...(plan ? { planApproval: 'required' as const } : {}),
    ...(verify && checks.length > 0
      ? { verify: { checks, onFail, ...(onFail === 'continue' ? { maxRounds: FIX_ROUNDS } : {}) } }
      : {}),
  };
}

/**
 * "Safety and checks" (WP-31 D) on the agent card: plan approval and the checks every run's result must pass, saved
 * with the agent and versioned with it. A dry run is chosen per message in the chat, never here.
 */
export function AgentSafetyChecks({ nodeId, stored, readOnly }: { nodeId: string; stored: unknown; readOnly?: boolean }) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const initial = agentGatesOf(stored);
  const [plan, setPlan] = useState(initial?.planApproval === 'required');
  const [verify, setVerify] = useState((initial?.verify?.checks.length ?? 0) > 0);
  const [onFail, setOnFail] = useState<'continue' | 'flag'>(initial?.verify?.onFail === 'flag' ? 'flag' : 'continue');
  const [rows, setRows] = useState<CheckRow[]>(() => (initial?.verify?.checks ?? []).map((check, i) => rowOf(i, check)));
  const nextId = useRef(rows.length);

  const commit = useCallback(
    (next: { plan?: boolean; verify?: boolean; rows?: CheckRow[]; onFail?: 'continue' | 'flag' }) => {
      const gates = gatesOf(next.plan ?? plan, next.verify ?? verify, next.rows ?? rows, next.onFail ?? onFail);
      if (next.plan !== undefined) setPlan(next.plan);
      if (next.verify !== undefined) setVerify(next.verify);
      if (next.rows !== undefined) setRows(next.rows);
      if (next.onFail !== undefined) setOnFail(next.onFail);
      setNodes((nodes) =>
        nodes.map((node) => {
          if (node.id !== nodeId) return node;
          const { gates: _previous, ...config } = (node.data.config as Record<string, unknown>) || {};
          return { ...node, data: { ...node.data, config: Object.keys(gates).length > 0 ? { ...config, gates } : config } };
        }),
      );
    },
    [nodeId, onFail, plan, rows, setNodes, verify],
  );

  const edit = (id: number, change: Partial<CheckRow>) =>
    commit({ rows: rows.map((row) => (row.id === id ? { ...row, ...change } : row)) });
  const add = () => {
    const id = nextId.current++;
    commit({ verify: true, rows: [...rows, { id, kind: 'citations', path: '', format: ANY_FORMAT, schema: '', command: '', timeoutS: '' }] });
  };

  return (
    <Flex direction="column" gap="3" data-testid="agent-safety-checks" style={{ padding: 'var(--space-2) var(--space-3) var(--space-3)' }}>
      <Flex align="start" justify="between" gap="3">
        <Flex direction="column" style={{ minWidth: 0 }}>
          <Text as="label" htmlFor={`${nodeId}-plan`} size="2" weight="medium">
            {t('agentBuilder.gates.planApproval')}
          </Text>
          <Text size="1" color="gray">
            {t('agentBuilder.gates.planApprovalHint')}
          </Text>
        </Flex>
        <Switch
          id={`${nodeId}-plan`}
          className="nodrag nopan"
          checked={plan}
          disabled={readOnly}
          onCheckedChange={(checked) => commit({ plan: checked })}
        />
      </Flex>

      <Flex direction="column" gap="2">
        <Flex align="start" justify="between" gap="3">
          <Flex direction="column" style={{ minWidth: 0 }}>
            <Text as="label" htmlFor={`${nodeId}-verify`} size="2" weight="medium">
              {t('agentBuilder.gates.verify')}
            </Text>
            <Text size="1" color="gray">
              {t('agentBuilder.gates.verifyHint')}
            </Text>
          </Flex>
          <Switch
            id={`${nodeId}-verify`}
            className="nodrag nopan"
            checked={verify}
            disabled={readOnly}
            onCheckedChange={(checked) => {
              if (checked && rows.length === 0) add();
              else commit({ verify: checked });
            }}
          />
        </Flex>

        {verify ? (
          <Flex direction="column" gap="2" data-testid="agent-checks">
            {rows.map((row) => {
              const read = checkOf(row);
              const label = t(`agentBuilder.gates.kind.${row.kind}`);
              return (
                <Flex key={row.id} direction="column" gap="1" data-testid="agent-check">
                  <Flex gap="2" wrap="wrap" align="center">
                    <Select.Root
                      value={row.kind}
                      disabled={readOnly}
                      onValueChange={(kind) => edit(row.id, { kind: kind as CheckKind })}
                    >
                      <Select.Trigger className="nodrag nopan" aria-label={t('agentBuilder.gates.kindLabel')} style={{ minHeight: 32 }} />
                      <Select.Content>
                        {KINDS.map((kind) => (
                          <Select.Item key={kind} value={kind}>
                            {t(`agentBuilder.gates.kind.${kind}`)}
                          </Select.Item>
                        ))}
                      </Select.Content>
                    </Select.Root>
                    {row.kind === 'file' || row.kind === 'json_schema' ? (
                      <TextField.Root
                        className="nodrag nopan"
                        value={row.path}
                        disabled={readOnly}
                        placeholder={t('agentBuilder.gates.pathPlaceholder')}
                        aria-label={t('agentBuilder.gates.pathLabel', { check: label })}
                        onChange={(e) => edit(row.id, { path: e.target.value })}
                        style={{ flex: '1 1 8rem', minWidth: 0 }}
                      />
                    ) : null}
                    {row.kind === 'file' ? (
                      <Select.Root
                        value={row.format}
                        disabled={readOnly}
                        onValueChange={(format) => edit(row.id, { format: format as CheckRow['format'] })}
                      >
                        <Select.Trigger className="nodrag nopan" aria-label={t('agentBuilder.gates.formatLabel')} style={{ minHeight: 32 }} />
                        <Select.Content>
                          <Select.Item value={ANY_FORMAT}>{t('agentBuilder.gates.anyFormat')}</Select.Item>
                          {FILE_FORMATS.map((format) => (
                            <Select.Item key={format} value={format}>
                              {format.toUpperCase()}
                            </Select.Item>
                          ))}
                        </Select.Content>
                      </Select.Root>
                    ) : null}
                    {row.kind === 'command' ? (
                      <>
                        <TextField.Root
                          className="nodrag nopan"
                          value={row.command}
                          disabled={readOnly}
                          placeholder="pytest -q"
                          aria-label={t('agentBuilder.gates.commandLabel')}
                          onChange={(e) => edit(row.id, { command: e.target.value })}
                          style={{ flex: '1 1 8rem', minWidth: 0 }}
                        />
                        <Flex align="center" gap="1">
                          <Text size="1" color="gray">
                            {t('agentBuilder.gates.within')}
                          </Text>
                          <TextField.Root
                            className="nodrag nopan"
                            value={row.timeoutS}
                            disabled={readOnly}
                            inputMode="numeric"
                            placeholder={String(DEFAULT_COMMAND_TIMEOUT_S)}
                            aria-label={t('agentBuilder.gates.timeoutLabel')}
                            onChange={(e) => edit(row.id, { timeoutS: e.target.value })}
                            style={{ width: 64 }}
                          />
                          <Text size="1" color="gray">
                            {t('agentBuilder.gates.seconds')}
                          </Text>
                        </Flex>
                      </>
                    ) : null}
                    {readOnly ? null : (
                      <IconButton
                        className="nodrag nopan"
                        variant="ghost"
                        color="gray"
                        aria-label={t('agentBuilder.gates.remove', { check: label })}
                        onClick={() => {
                          const left = rows.filter((other) => other.id !== row.id);
                          commit({ rows: left, ...(left.length === 0 ? { verify: false } : {}) });
                        }}
                        style={{ minWidth: 32, minHeight: 32 }}
                      >
                        <MaterialIcon name="close" size={16} color="var(--gray-11)" />
                      </IconButton>
                    )}
                  </Flex>
                  {row.kind === 'json_schema' ? (
                    <TextArea
                      className="nodrag nopan"
                      value={row.schema}
                      disabled={readOnly}
                      placeholder={t('agentBuilder.gates.schemaPlaceholder')}
                      aria-label={t('agentBuilder.gates.schemaLabel')}
                      onChange={(e) => edit(row.id, { schema: e.target.value })}
                      rows={3}
                    />
                  ) : null}
                  {'problem' in read ? (
                    <Text size="1" color="red" role="alert">
                      {t(read.problem)}
                    </Text>
                  ) : null}
                </Flex>
              );
            })}
            {readOnly || rows.length >= MAX_CHECKS ? null : (
              <Button className="nodrag nopan" variant="soft" size="1" onClick={add} style={{ alignSelf: 'flex-start', minHeight: 32 }}>
                <MaterialIcon name="add" size={14} />
                {t('agentBuilder.gates.addCheck')}
              </Button>
            )}
            <Flex align="center" gap="2" wrap="wrap">
              <Text size="1" color="gray">
                {t('agentBuilder.gates.whenFails')}
              </Text>
              <Select.Root
                value={onFail}
                disabled={readOnly}
                onValueChange={(value) => commit({ onFail: value as 'continue' | 'flag' })}
              >
                <Select.Trigger className="nodrag nopan" aria-label={t('agentBuilder.gates.whenFails')} style={{ minHeight: 32 }} />
                <Select.Content>
                  <Select.Item value="continue">{t('agentBuilder.gates.onFailContinue', { rounds: FIX_ROUNDS })}</Select.Item>
                  <Select.Item value="flag">{t('agentBuilder.gates.onFailFlag')}</Select.Item>
                </Select.Content>
              </Select.Root>
            </Flex>
          </Flex>
        ) : null}
      </Flex>
    </Flex>
  );
}
