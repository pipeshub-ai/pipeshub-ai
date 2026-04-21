'use client';

import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, useReactFlow, useStore, useNodeConnections } from '@xyflow/react';
import { Box, Flex, Text, IconButton, Dialog, Button, TextArea, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import type { FlowNodeData } from '../types';
import {
  AGENT_KNOWLEDGE_FALLBACK_ICON,
  AGENT_LLM_FALLBACK_ICON,
  AGENT_TOOLSET_FALLBACK_ICON,
  normalizeDisplayName,
  resolveNodeHeaderIconUrl,
} from '../display-utils';
import { FLOW_NODE_CARD, FLOW_NODE_PANEL_BG, FLOW_NODE_WELL } from '../flow-theme';

type ChipIconKind = 'knowledge' | 'toolset' | 'llm';

const CHIP_ICON_FALLBACK: Record<ChipIconKind, string> = {
  knowledge: AGENT_KNOWLEDGE_FALLBACK_ICON,
  toolset: AGENT_TOOLSET_FALLBACK_ICON,
  llm: AGENT_LLM_FALLBACK_ICON,
};

function CoreHandle({
  type,
  position,
  id,
  nodeDataId,
  offsetStyle,
}: {
  type: 'source' | 'target';
  position: Position;
  id: string;
  nodeDataId: string;
  offsetStyle: React.CSSProperties;
}) {
  const connections = useNodeConnections({ id: nodeDataId, handleType: type, handleId: id });
  const isConnected = connections.length > 0;

  return (
    <Handle
      type={type}
      position={position}
      id={id}
      className="agent-builder-node-handle"
      data-connected={isConnected ? 'true' : 'false'}
      style={{
        top: '50%',
        width: 13,
        height: 13,
        background: FLOW_NODE_PANEL_BG,
        border: '1.5px solid var(--gray-6)',
        boxShadow: `0 0 0 1px ${FLOW_NODE_PANEL_BG}, 0 1px 2px var(--gray-a4)`,
        borderRadius: '50%',
        zIndex: 50,
        ...offsetStyle,
      }}
    />
  );
}

function getModelLabel(cfg: Record<string, unknown> | undefined): string {
  if (!cfg) return '';
  const f = (cfg.modelFriendlyName as string)?.trim();
  if (f) return f;
  return ((cfg.modelName as string) || '').trim();
}

/** Prefer configured instance name so multiple toolsets of the same type stay distinguishable. */
function toolsetConnectionChipLabel(n: FlowNodeData): string {
  const c = (n.config || {}) as Record<string, unknown>;
  const inst = String(c.instanceName ?? '').trim();
  const disp = String(c.displayName ?? '').trim();
  return normalizeDisplayName(inst || disp || n.label);
}

type CoreInboundHandle = 'input' | 'llms' | 'knowledge' | 'toolsets';

function inboundHandleForEdge(
  targetHandle: string | null | undefined,
  source: FlowNodeData
): CoreInboundHandle | null {
  const h = targetHandle as CoreInboundHandle | undefined;
  if (h === 'input' || h === 'llms' || h === 'knowledge' || h === 'toolsets') return h;

  const t = source.type;
  if (t === 'user-input') return 'input';
  if (t.startsWith('llm-')) return 'llms';
  if (
    t.startsWith('toolset-') ||
    t.startsWith('tool-group-') ||
    (t.startsWith('tool-') && !t.startsWith('tool-group-'))
  ) {
    return 'toolsets';
  }
  if (t === 'kb-group' || t.startsWith('kb-') || t === 'app-group' || t.startsWith('app-')) {
    return 'knowledge';
  }
  return null;
}

/** Explicit surface so labels stay visible inside React Flow + nested panels (avoids soft-Badge contrast issues). */
function ConnectionChip({
  label,
  variant = 'default',
  onClick,
  iconSrc,
  iconFallbackSrc = AGENT_KNOWLEDGE_FALLBACK_ICON,
}: {
  label: string;
  variant?: 'default' | 'more';
  /** When set (e.g. overflow chip), the chip is keyboard-activatable and expands/collapses the list. */
  onClick?: () => void;
  /** Connector / collection icon (legacy agent builder parity). */
  iconSrc?: string;
  /** Replacement if the image fails to load (knowledge vs toolsets use different neutrals). */
  iconFallbackSrc?: string;
}) {
  const isMore = variant === 'more';
  const surfaceStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
    minWidth: 0,
    boxSizing: 'border-box',
    padding: '4px 10px',
    borderRadius: 'var(--radius-full)',
    border: isMore ? '1px dashed var(--gray-8)' : '1px solid var(--gray-7)',
    background: isMore ? 'var(--gray-a3)' : 'var(--gray-a4)',
    boxShadow: isMore ? 'none' : 'inset 0 1px 0 var(--gray-a2)',
    cursor: onClick ? 'pointer' : undefined,
    font: 'inherit',
    color: 'inherit',
  };

  const iconEl =
    iconSrc && variant === 'default' ? (
      <ThemeableAssetIcon
        {...themeableAssetIconPresets.flowNodeWell}
        src={iconSrc}
        size={14}
        color="var(--agent-flow-text)"
        fallbackSrc={iconFallbackSrc}
      />
    ) : null;

  const text = (
    <Text
      as="span"
      size="1"
      weight="medium"
      style={{
        color: 'var(--agent-flow-text)',
        lineHeight: '18px',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        minWidth: 0,
      }}
    >
      {label}
    </Text>
  );

  const body = (
    <Flex align="center" gap="2" style={{ minWidth: 0 }}>
      {iconEl}
      {text}
    </Flex>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className="agent-core-connection-chip"
        title={label}
        aria-label={label}
        onClick={onClick}
        style={surfaceStyle}
      >
        {body}
      </button>
    );
  }

  return (
    <Box className="agent-core-connection-chip" title={label} style={surfaceStyle}>
      {body}
    </Box>
  );
}

const MAX_VISIBLE = { models: 5, knowledge: 5, toolsets: 5, input: 4 } as const;

function ConnectedChips({
  nodes,
  max,
  labelOf,
  chipIconKind,
}: {
  nodes: FlowNodeData[];
  max: number;
  labelOf: (n: FlowNodeData) => string;
  /** When set, chips show connector/collection icons with the correct error fallback. */
  chipIconKind?: ChipIconKind;
}) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  if (!nodes.length) return null;
  const overflow = nodes.length - max;
  const limit = showAll ? nodes.length : max;
  const shown = nodes.slice(0, limit);
  const iconFallback = chipIconKind ? CHIP_ICON_FALLBACK[chipIconKind] : undefined;
  const iconSrcFor = chipIconKind
    ? (node: FlowNodeData) => resolveNodeHeaderIconUrl(node) ?? CHIP_ICON_FALLBACK[chipIconKind]
    : undefined;
  return (
    <Flex wrap="wrap" gap="2" style={{ alignSelf: 'stretch' }}>
      {shown.map((n) => (
        <ConnectionChip
          key={n.id}
          label={labelOf(n)}
          iconSrc={iconSrcFor?.(n)}
          iconFallbackSrc={iconFallback}
        />
      ))}
      {overflow > 0 ? (
        <ConnectionChip
          variant="more"
          label={
            showAll ? t('agentBuilder.showFewerTools') : t('agentBuilder.moreItems', { count: overflow })
          }
          onClick={() => setShowAll((v) => !v)}
        />
      ) : null}
    </Flex>
  );
}

export function AgentCoreNode({
  data,
  selected,
  readOnly,
}: {
  id?: string;
  data: FlowNodeData;
  selected: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const storeNodes = useStore((s) => s.nodes);
  const storeEdges = useStore((s) => s.edges);

  const [promptOpen, setPromptOpen] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState(
    (data.config?.systemPrompt as string) || t('agentBuilder.defaultSystemPrompt')
  );
  const [instructions, setInstructions] = useState((data.config?.instructions as string) || '');
  const [startMessage, setStartMessage] = useState(
    (data.config?.startMessage as string) || t('agentBuilder.defaultStartMessage')
  );

  const connected = useMemo(() => {
    const incoming = storeEdges.filter((e) => e.target === data.id);
    const map: Record<CoreInboundHandle, FlowNodeData[]> = {
      input: [],
      toolsets: [],
      knowledge: [],
      llms: [],
    };
    incoming.forEach((e) => {
      const source = storeNodes.find((n) => n.id === e.source);
      const fd = source?.data as FlowNodeData | undefined;
      if (!fd) return;
      const handle = inboundHandleForEdge(e.targetHandle, fd);
      if (handle) {
        const list = map[handle];
        if (!list.some((x) => x.id === fd.id)) list.push(fd);
      }
    });
    return map;
  }, [data.id, storeEdges, storeNodes]);

  const savePrompts = useCallback(() => {
    setNodes((nodes) =>
      nodes.map((node) =>
        node.id === data.id
          ? {
              ...node,
              data: {
                ...node.data,
                config: {
                  ...((node.data.config as Record<string, unknown>) || {}),
                  systemPrompt,
                  instructions,
                  startMessage,
                },
              },
            }
          : node
      )
    );
    setPromptOpen(false);
  }, [data.id, instructions, setNodes, startMessage, systemPrompt]);

  const openPrompts = () => {
    setSystemPrompt((data.config?.systemPrompt as string) || t('agentBuilder.defaultSystemPrompt'));
    setInstructions((data.config?.instructions as string) || '');
    setStartMessage((data.config?.startMessage as string) || t('agentBuilder.defaultStartMessage'));
    setPromptOpen(true);
  };

  return (
    <>
      <div className="flow-node-card">
      <Box
        className="flow-node-surface"
        style={{
          width: 340,
          boxSizing: 'border-box',
          borderRadius: FLOW_NODE_CARD.radius,
          border: selected ? '1px solid var(--gray-11)' : FLOW_NODE_CARD.borderIdle,
          background: FLOW_NODE_PANEL_BG,
          boxShadow: selected ? FLOW_NODE_CARD.shadowSelected : FLOW_NODE_CARD.shadow,
          overflow: 'visible',
        }}
      >
        <Flex
          align="center"
          justify="between"
          px="3"
          py="2"
          gap="2"
          style={{
            borderBottom: '1px solid var(--agent-flow-node-border)',
            background: 'var(--agent-flow-node-header-bg)',
          }}
        >
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
            <Box
              style={{
                width: 40,
                height: 40,
                borderRadius: 'var(--radius-2)',
                background: 'var(--gray-a2)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: '1px solid var(--gray-6)',
                flexShrink: 0,
                boxShadow: 'inset 0 1px 0 var(--gray-a3)',
              }}
            >
              <MaterialIcon name="auto_awesome" size={22} color="var(--agent-flow-text)" />
            </Box>
            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
              <Flex align="center" gap="2" wrap="wrap">
                <Text weight="bold" style={{ color: 'var(--agent-flow-text)', lineHeight: '22px', fontSize: 15 }}>
                  {t('agentBuilder.coreNodeTitle')}
                </Text>
                <Badge size="1" variant="soft" color="gray" highContrast>
                  {t('agentBuilder.coreNodeBadge')}
                </Badge>
              </Flex>
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}>
                {t('agentBuilder.coreNodeSubtitle')}
              </Text>
            </Flex>
          </Flex>
          {!readOnly ? (
            <IconButton size="2" variant="soft" color="gray" onClick={openPrompts} aria-label={t('agentBuilder.editPrompts')}>
              <MaterialIcon name="edit" size={18} color="var(--agent-flow-text)" />
            </IconButton>
          ) : null}
        </Flex>

        <Box
          p="3"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            alignItems: 'stretch',
            background: FLOW_NODE_PANEL_BG,
          }}
        >
          <Box style={{ width: '100%', minWidth: 0 }}>
            <Text
              size="1"
              weight="medium"
              style={{ display: 'block', color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}
            >
              {t('agentBuilder.systemPromptLabel')}
            </Text>
            <Box
              p="2"
              mt="1"
              style={{
                width: '100%',
                boxSizing: 'border-box',
                minHeight: 44,
                borderRadius: FLOW_NODE_WELL.radius,
                border: FLOW_NODE_WELL.border,
                background: FLOW_NODE_WELL.background,
              }}
            >
              <Text
                size="1"
                style={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 52,
                  overflow: 'hidden',
                  color: 'var(--agent-flow-text)',
                  lineHeight: 1.5,
                }}
              >
                {(data.config?.systemPrompt as string) || '—'}
              </Text>
            </Box>
          </Box>
          <Box style={{ width: '100%', minWidth: 0 }}>
            <Text
              size="1"
              weight="medium"
              style={{ display: 'block', color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}
            >
              {t('agentBuilder.startMessageLabel')}
            </Text>
            <Box
              p="2"
              mt="1"
              style={{
                width: '100%',
                boxSizing: 'border-box',
                minHeight: 36,
                borderRadius: FLOW_NODE_WELL.radius,
                border: FLOW_NODE_WELL.border,
                background: FLOW_NODE_WELL.background,
              }}
            >
              <Text
                size="1"
                style={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 40,
                  overflow: 'hidden',
                  color: 'var(--agent-flow-text)',
                  lineHeight: 1.5,
                }}
              >
                {(data.config?.startMessage as string) || '—'}
              </Text>
            </Box>
          </Box>

          <Section title={t('agentBuilder.modelSection')} icon="psychology">
            <CoreHandle type="target" position={Position.Left} id="llms" nodeDataId={data.id} offsetStyle={{ left: -8 }} />
            {connected.llms.length ? (
              <ConnectedChips
                nodes={connected.llms}
                max={MAX_VISIBLE.models}
                labelOf={(n) => getModelLabel(n.config) || n.label}
                chipIconKind="llm"
              />
            ) : (
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', fontStyle: 'italic' }}>
                {t('agentBuilder.connectModel')}
              </Text>
            )}
          </Section>

          <Section title={t('agentBuilder.knowledge')} icon="library_books">
            <CoreHandle type="target" position={Position.Left} id="knowledge" nodeDataId={data.id} offsetStyle={{ left: -8 }} />
            {connected.knowledge.length ? (
              <ConnectedChips
                nodes={connected.knowledge}
                max={MAX_VISIBLE.knowledge}
                labelOf={(n) =>
                  normalizeDisplayName(
                    (n.config?.kbName as string) || (n.config?.appName as string) || n.label
                  )
                }
                chipIconKind="knowledge"
              />
            ) : (
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', fontStyle: 'italic' }}>
                {t('agentBuilder.optional')}
              </Text>
            )}
          </Section>

          <Section title={t('agentBuilder.toolsetsSection')} icon="extension">
            <CoreHandle type="target" position={Position.Left} id="toolsets" nodeDataId={data.id} offsetStyle={{ left: -8 }} />
            {connected.toolsets.length ? (
              <ConnectedChips
                nodes={connected.toolsets}
                max={MAX_VISIBLE.toolsets}
                labelOf={toolsetConnectionChipLabel}
                chipIconKind="toolset"
              />
            ) : (
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', fontStyle: 'italic' }}>
                {t('agentBuilder.optional')}
              </Text>
            )}
          </Section>

          <Section title={t('agentBuilder.inputSection')} icon="forum">
            <CoreHandle type="target" position={Position.Left} id="input" nodeDataId={data.id} offsetStyle={{ left: -8 }} />
            {connected.input.length ? (
              <ConnectedChips nodes={connected.input} max={MAX_VISIBLE.input} labelOf={(n) => n.label} />
            ) : (
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', fontStyle: 'italic' }}>
                {t('agentBuilder.connectChatInput')}
              </Text>
            )}
          </Section>

          <Section title={t('agentBuilder.responseSection')} icon="reply">
            <CoreHandle type="source" position={Position.Right} id="response" nodeDataId={data.id} offsetStyle={{ right: -8 }} />
            <Flex align="center" gap="2">
              <MaterialIcon name="arrow_forward" size={14} color="var(--agent-flow-text-muted)" />
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
                {t('agentBuilder.toChatOutput')}
              </Text>
            </Flex>
          </Section>
        </Box>
      </Box>
      </div>

      <Dialog.Root open={promptOpen} onOpenChange={setPromptOpen}>
        <Dialog.Content style={{ maxWidth: 540 }}>
          <Dialog.Title>{t('agentBuilder.agentConfigTitle')}</Dialog.Title>
          <Flex direction="column" gap="3" mt="2">
            <Box>
              <Text size="2" weight="bold" mb="1">
                {t('agentBuilder.systemPromptLabel')}
              </Text>
              <TextArea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={4}
                style={{ width: '100%' }}
              />
            </Box>
            <Box>
              <Text size="2" weight="bold" mb="1">
                {t('agentBuilder.instructionsLabel')}
              </Text>
              <TextArea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={3}
                style={{ width: '100%' }}
              />
            </Box>
            <Box>
              <Text size="2" weight="bold" mb="1">
                {t('agentBuilder.startingMessageLabel')}
              </Text>
              <TextArea
                value={startMessage}
                onChange={(e) => setStartMessage(e.target.value)}
                rows={2}
                style={{ width: '100%' }}
              />
            </Box>
            <Flex gap="2" justify="end">
              <Dialog.Close>
                <Button variant="soft" color="gray">
                  {t('action.cancel')}
                </Button>
              </Dialog.Close>
              <Button onClick={savePrompts}>{t('action.save')}</Button>
            </Flex>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: string;
  children: React.ReactNode;
}) {
  return (
    <Box
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        minWidth: 0,
        boxSizing: 'border-box',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--agent-flow-node-border)',
        background: FLOW_NODE_PANEL_BG,
        boxShadow: '0 1px 0 var(--gray-a4)',
        overflow: 'visible',
        position: 'relative',
      }}
    >
      <Flex
        align="center"
        gap="2"
        px="2"
        py="1"
        style={{
          borderBottom: '1px solid var(--agent-flow-node-border)',
          background: 'var(--agent-flow-section-header-bg)',
        }}
      >
        <Box
          style={{
            width: 24,
            height: 24,
            borderRadius: 'var(--radius-1)',
            background: 'var(--gray-a2)',
            border: '1px solid var(--gray-6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={icon} size={14} color="var(--agent-flow-text-muted)" />
        </Box>
        <Text size="1" weight="medium" style={{ color: 'var(--agent-flow-text)', lineHeight: '18px' }}>
          {title}
        </Text>
      </Flex>
      <Box px="2" py="2" style={{ display: 'flex', flexDirection: 'column', gap: 8, position: 'relative' }}>
        {children}
      </Box>
    </Box>
  );
}
