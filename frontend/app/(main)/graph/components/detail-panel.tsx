'use client';

import { Badge, Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { GraphModel } from '../model/graph-model';
import type { NodeId } from '../types';

export interface DetailPanelProps {
  model: GraphModel;
  selection: NodeId | null;
  onOpen: (id: NodeId) => void;
  onExpand: (id: NodeId) => void;
  onShowMore: (id: NodeId) => void;
  /** The node that was selected has been taken away by an access re-check. */
  selectionGone?: boolean;
}

/** What is known about the selected node. A node that left the model leaves the panel with it. */
export function DetailPanel({ model, selection, onOpen, onExpand, onShowMore, selectionGone = false }: DetailPanelProps) {
  const { t } = useTranslation();
  const node = selection ? model.nodes[selection] : undefined;
  if (!node) return <Text color="gray">{selectionGone ? t('graph.ui.selectionGone') : t('graph.ui.nothingSelected')}</Text>;
  const total = model.totals[node.id];
  return (
    <Flex direction="column" gap="2" aria-label={t('graph.ui.details')} role="region">
      <Heading size="3" as="h2">{node.label ?? node.id}</Heading>
      <Flex gap="2" align="center">
        {node.kind ? <Badge color="gray" highContrast>{node.kind}</Badge> : null}
        <Text size="1" color="gray">
          {node.file}
          {node.line ? `:${node.line}` : ''}
        </Text>
      </Flex>
      {total !== undefined ? <Text size="2">{t('graph.ui.links', { n: total })}</Text> : null}
      <Flex gap="2" wrap="wrap">
        <Button size="2" variant="soft" highContrast title={t('graph.ui.openHint')} onClick={() => onOpen(node.id)}>
          {t('graph.ui.open')}
        </Button>
        <Button size="2" variant="soft" highContrast title={t('graph.ui.expandHint')} onClick={() => onExpand(node.id)}>
          {t('graph.ui.expand')}
        </Button>
        {model.more[node.id] ? (
          <Button size="2" variant="outline" highContrast onClick={() => onShowMore(node.id)}>
            {t('graph.ui.showMore')}
          </Button>
        ) : null}
      </Flex>
    </Flex>
  );
}
