'use client';

import { useState } from 'react';
import { Badge, Button, Flex, Table, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { GraphModel } from '../model/graph-model';
import type { NodeId } from '../types';

export interface TableTwinProps {
  model: GraphModel;
  selection: NodeId | null;
  /** The node the view is centred on: listed first and marked. */
  focus?: NodeId | null;
  onSelect: (id: NodeId) => void;
  onOpen: (id: NodeId) => void;
  onExpand: (id: NodeId) => void;
  onShowMore: (id: NodeId) => void;
}

/** Rows drawn at a time; the rest come with "Show more rows", so a view of thousands stays quick. */
export const TABLE_PAGE = 200;

/**
 * The same nodes as the canvas, with the same actions, usable by keyboard and by a screen
 * reader. It is the accessible path, not a fallback: anything the canvas can do is here.
 * The links counted are the ones drawn, so the numbers carry nothing about hidden items,
 * and the headers say they are the ones shown, not totals.
 */
export function TableTwin({ model, selection, focus = null, onSelect, onOpen, onExpand, onShowMore }: TableTwinProps) {
  const { t } = useTranslation();
  const [shown, setShown] = useState(TABLE_PAGE);
  const callers: Record<string, number> = {};
  const callees: Record<string, number> = {};
  for (const e of Object.values(model.edges)) {
    callees[e.from] = (callees[e.from] ?? 0) + 1;
    callers[e.to] = (callers[e.to] ?? 0) + 1;
  }
  const all = Object.values(model.nodes).sort(
    (a, b) => Number(b.id === focus) - Number(a.id === focus) || (a.label ?? a.id).localeCompare(b.label ?? b.id),
  );
  const rows = all.slice(0, shown);

  return (
    <>
      <Table.Root size="1" variant="surface" aria-label={t('graph.ui.table.caption')}>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeaderCell>{t('graph.ui.table.name')}</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>{t('graph.ui.table.kind')}</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>{t('graph.ui.table.file')}</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>{t('graph.ui.table.callersShown')}</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>{t('graph.ui.table.calleesShown')}</Table.ColumnHeaderCell>
            <Table.ColumnHeaderCell>{t('graph.ui.table.actions')}</Table.ColumnHeaderCell>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {rows.map((node) => (
            <Table.Row
              key={node.id}
              aria-selected={selection === node.id}
              onClick={() => onSelect(node.id)}
              style={selection === node.id ? { background: 'var(--accent-3)' } : undefined}
            >
              <Table.RowHeaderCell>
                <Flex gap="2" align="center">
                  <Text weight={selection === node.id || focus === node.id ? 'bold' : 'regular'}>{node.label ?? node.id}</Text>
                  {focus === node.id ? <Badge color="jade">{t('graph.ui.table.focus')}</Badge> : null}
                </Flex>
              </Table.RowHeaderCell>
              <Table.Cell>{node.kind ? <Badge color="gray" highContrast>{node.kind}</Badge> : null}</Table.Cell>
              <Table.Cell>
                <Text size="1" color="gray">
                  {node.file}
                  {node.line ? `:${node.line}` : ''}
                </Text>
              </Table.Cell>
              <Table.Cell>{model.expanded[node.id] ? (callers[node.id] ?? 0) : '–'}</Table.Cell>
              <Table.Cell>{model.expanded[node.id] ? (callees[node.id] ?? 0) : '–'}</Table.Cell>
              <Table.Cell>
                <Flex gap="2" wrap="wrap">
                  <Button size="1" variant="soft" highContrast title={t('graph.ui.openHint')} onClick={() => onOpen(node.id)}>
                    {t('graph.ui.open')}
                  </Button>
                  <Button size="1" variant="soft" highContrast title={t('graph.ui.expandHint')} onClick={() => onExpand(node.id)}>
                    {t('graph.ui.expand')}
                  </Button>
                  {model.more[node.id] ? (
                    <Button size="1" variant="outline" highContrast onClick={() => onShowMore(node.id)}>
                      {t('graph.ui.showMore')}
                    </Button>
                  ) : null}
                </Flex>
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
      {all.length > shown ? (
        <Button size="2" variant="soft" onClick={() => setShown((n) => n + TABLE_PAGE)}>
          {t('graph.ui.table.moreRows')}
        </Button>
      ) : null}
    </>
  );
}
