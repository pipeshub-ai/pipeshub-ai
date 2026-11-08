'use client';

import { Flex, IconButton, SegmentedControl, Switch, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Direction } from '../types';

export interface ToolbarProps {
  direction: Direction;
  includeTests: boolean;
  canBack: boolean;
  canForward: boolean;
  /** Something is drawn: Fit and the view switch have nothing to act on before that. */
  hasGraph: boolean;
  tableView: boolean;
  /** The table is the only view (the meter ran out of steps, or an assistive tech is in use). */
  tableOnly: boolean;
  onDirection: (direction: Direction) => void;
  onIncludeTests: (include: boolean) => void;
  onBack: () => void;
  onForward: () => void;
  onFit: () => void;
  onToggleView: () => void;
}

export function Toolbar(props: ToolbarProps) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="3" wrap="wrap" role="toolbar" aria-label={t('graph.title')}>
      <Tooltip content={t('graph.ui.back')}>
        <IconButton variant="soft" size="2" disabled={!props.canBack} aria-label={t('graph.ui.back')} onClick={props.onBack}>
          <span aria-hidden="true"><MaterialIcon name="arrow_back" size={18} /></span>
        </IconButton>
      </Tooltip>
      <Tooltip content={t('graph.ui.forward')}>
        <IconButton variant="soft" size="2" disabled={!props.canForward} aria-label={t('graph.ui.forward')} onClick={props.onForward}>
          <span aria-hidden="true"><MaterialIcon name="arrow_forward" size={18} /></span>
        </IconButton>
      </Tooltip>
      <div style={{ maxWidth: '100%', overflowX: 'auto' }}>
        <SegmentedControl.Root size="2" value={props.direction} onValueChange={(v) => props.onDirection(v as Direction)}>
          <SegmentedControl.Item value="any">{t('graph.ui.direction.any')}</SegmentedControl.Item>
          <SegmentedControl.Item value="inbound">{t('graph.ui.direction.inbound')}</SegmentedControl.Item>
          <SegmentedControl.Item value="outbound">{t('graph.ui.direction.outbound')}</SegmentedControl.Item>
        </SegmentedControl.Root>
      </div>
      <Text as="label" size="2">
        <Flex gap="2" align="center">
          <Switch checked={props.includeTests} onCheckedChange={props.onIncludeTests} />
          {t('graph.ui.includeTests')}
        </Flex>
      </Text>
      {!props.tableView && !props.tableOnly ? (
        <Tooltip content={t('graph.ui.fit')}>
          <IconButton variant="soft" size="2" disabled={!props.hasGraph} aria-label={t('graph.ui.fit')} onClick={props.onFit}>
            <span aria-hidden="true"><MaterialIcon name="fit_screen" size={18} /></span>
          </IconButton>
        </Tooltip>
      ) : null}
      {!props.tableOnly ? (
        <Tooltip content={props.tableView ? t('graph.ui.showGraph') : t('graph.ui.showTable')}>
          <IconButton
            variant="soft"
            size="2"
            disabled={!props.hasGraph}
            aria-label={props.tableView ? t('graph.ui.showGraph') : t('graph.ui.showTable')}
            onClick={props.onToggleView}
          >
            <span aria-hidden="true"><MaterialIcon name={props.tableView ? 'hub' : 'table_rows'} size={18} /></span>
          </IconButton>
        </Tooltip>
      ) : null}
    </Flex>
  );
}
