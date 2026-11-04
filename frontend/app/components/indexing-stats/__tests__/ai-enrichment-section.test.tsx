import React, { useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/workspace/connectors/components/instance-panel/overview-stats-shimmer', () => ({
  OverviewStatsGridShimmer: () => null,
  OverviewRecordTypesShimmer: () => null,
  OverviewTypesBadgeShimmer: () => null,
}));

import { AiEnrichmentSection, type AiEnrichmentSectionProps } from '../ai-enrichment-section';
import { IndexingStatsPanel } from '../indexing-stats-panel';
import type { StageProgress } from '../derive-records-status';
import type { ConnectorStatsResponse, ExtractionStatusCounts } from '@/app/(main)/workspace/connectors/types';

const progress = (p: Partial<StageProgress>): StageProgress => ({
  completed: 0,
  waiting: 0,
  failed: 0,
  skipped: 0,
  ...p,
});

function Harness(props: Partial<AiEnrichmentSectionProps> & { initiallyOpen?: boolean }) {
  const { initiallyOpen = false, ...rest } = props;
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <Theme>
      <AiEnrichmentSection
        classification={progress({ completed: 87, waiting: 20 })}
        entities={null}
        open={open}
        onOpenChange={setOpen}
        {...rest}
      />
    </Theme>
  );
}

const toggle = () => screen.getByRole('button', { name: /Data extraction with AI/ });
const meter = (name: string) => screen.getByRole('progressbar', { name });

afterEach(() => {
  cleanup();
});

describe('AiEnrichmentSection', () => {
  it('starts collapsed and opens on click', () => {
    render(<Harness />);

    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('progressbar')).toBeNull();

    fireEvent.click(toggle());

    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/Search doesn't wait for it/)).toBeTruthy();
    expect(meter('Classification').getAttribute('aria-valuetext')).toBe('87 of 107');
  });

  it('shows progress over the records the stage applies to, without the skipped ones', () => {
    render(<Harness initiallyOpen classification={progress({ completed: 80, waiting: 15, failed: 5, skipped: 12 })} />);

    const bar = meter('Classification');
    expect(bar.getAttribute('aria-valuenow')).toBe('80');
    expect(bar.getAttribute('aria-valuemax')).toBe('100');
    expect([...bar.querySelectorAll('[data-segment]')].map((s) => s.getAttribute('data-segment'))).toEqual([
      'completed',
      'failed',
      'waiting',
    ]);
    expect(screen.getByText('Skipped')).toBeTruthy();
    expect(screen.getByText('12')).toBeTruthy();
  });

  it('hides legend entries that are zero, except completed', () => {
    render(<Harness initiallyOpen classification={progress({ completed: 0, waiting: 4 })} />);

    expect(screen.getByText('Completed')).toBeTruthy();
    expect(screen.getByText('Waiting')).toBeTruthy();
    expect(screen.queryByText('Failed')).toBeNull();
    expect(screen.queryByText('Skipped')).toBeNull();
  });

  it('badges failures on the collapsed header, summed across stages', () => {
    render(
      <Harness
        classification={progress({ completed: 10, failed: 3 })}
        entities={progress({ completed: 5, waiting: 2, failed: 4 })}
      />
    );

    expect(within(toggle()).getByText('7 failed')).toBeTruthy();
  });

  it('badges work in progress, then done', () => {
    const { rerender } = render(<Harness classification={progress({ completed: 10, waiting: 1 })} />);
    expect(within(toggle()).getByText('In progress')).toBeTruthy();

    rerender(<Harness classification={progress({ completed: 10, skipped: 2 })} />);
    expect(within(toggle()).getByText('Done')).toBeTruthy();
  });

  it('shows no badge before any record reaches the stage', () => {
    render(<Harness initiallyOpen classification={progress({})} />);

    expect(within(toggle()).queryByText(/failed|In progress|Done/)).toBeNull();
    expect(screen.getByText('Starts once records are searchable')).toBeTruthy();
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('shows only the skipped count when every record skipped the stage', () => {
    render(<Harness initiallyOpen classification={progress({ skipped: 107 })} />);

    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.queryByText('Starts once records are searchable')).toBeNull();
    expect(screen.getByText('107')).toBeTruthy();
    expect(screen.getByText('Skipped')).toBeTruthy();
  });

  it('shows the entity extraction row only when that stage did work', () => {
    const { rerender } = render(<Harness initiallyOpen entities={progress({ skipped: 50 })} />);
    expect(screen.queryByText('Entity extraction')).toBeNull();

    rerender(<Harness initiallyOpen entities={progress({ completed: 1 })} />);
    expect(screen.getByText('Entity extraction')).toBeTruthy();
    expect(meter('Entity extraction').getAttribute('aria-valuetext')).toBe('1 of 1');
  });

  it('offers a retry only on a stage with failures', () => {
    const onRetryFailedClassification = vi.fn();
    const onRetryFailedEntities = vi.fn();
    render(
      <Harness
        initiallyOpen
        classification={progress({ completed: 10, failed: 2 })}
        entities={progress({ completed: 3 })}
        onRetryFailedClassification={onRetryFailedClassification}
        onRetryFailedEntities={onRetryFailedEntities}
      />
    );

    expect(screen.queryByRole('button', { name: /Retry failed entity extraction/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry failed classification (2)' }));
    expect(onRetryFailedClassification).toHaveBeenCalledTimes(1);
  });

  it('hides the retry when no handler is given', () => {
    render(<Harness initiallyOpen classification={progress({ completed: 10, failed: 2 })} />);

    expect(screen.queryByRole('button', { name: /Retry failed classification/ })).toBeNull();
  });

  it('disables the retry while it or another reindex runs', () => {
    const { rerender } = render(
      <Harness
        initiallyOpen
        classification={progress({ failed: 2 })}
        onRetryFailedClassification={() => {}}
        retryDisabled
      />
    );
    const retry = () => screen.getByRole('button', { name: /Retry failed classification/ }) as HTMLButtonElement;
    expect(retry().disabled).toBe(true);

    rerender(
      <Harness
        initiallyOpen
        classification={progress({ failed: 2 })}
        onRetryFailedClassification={() => {}}
        isRetryClassificationBusy
      />
    );
    expect(retry().disabled).toBe(true);
  });
});

function statsWith(extractionStatus?: ExtractionStatusCounts): ConnectorStatsResponse['data'] {
  return {
    orgId: 'org-1',
    connectorId: 'conn-1',
    origin: 'CONNECTOR',
    stats: {
      total: 10,
      indexingStatus: { COMPLETED: 10 } as unknown as ConnectorStatsResponse['data']['stats']['indexingStatus'],
      ...(extractionStatus ? { extractionStatus } : {}),
    },
    byRecordType: [],
  };
}

describe('IndexingStatsPanel: Data extraction with AI', () => {
  it('is absent when the backend sends no classification counts', () => {
    render(
      <Theme>
        <IndexingStatsPanel stats={statsWith()} />
      </Theme>
    );

    expect(screen.queryByRole('button', { name: /Data extraction with AI/ })).toBeNull();
  });

  it('stays open across a refresh', () => {
    const stats = statsWith({ COMPLETED: 6, QUEUED: 4 });
    const { rerender } = render(
      <Theme>
        <IndexingStatsPanel stats={stats} />
      </Theme>
    );
    fireEvent.click(toggle());

    rerender(
      <Theme>
        <IndexingStatsPanel stats={stats} isRefreshBusy />
      </Theme>
    );
    expect(screen.queryByRole('button', { name: /Data extraction with AI/ })).toBeNull();

    rerender(
      <Theme>
        <IndexingStatsPanel stats={stats} />
      </Theme>
    );
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
  });
});
