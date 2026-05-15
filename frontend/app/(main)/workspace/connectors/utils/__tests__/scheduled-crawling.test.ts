import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildScheduledCrawlingRemovePath,
  shouldRemoveScheduledCrawlingJobOnSyncDisable,
} from '../scheduled-crawling.ts';

const scheduledConfig = {
  config: {
    sync: {
      selectedStrategy: 'SCHEDULED',
      scheduledConfig: { intervalMinutes: 60 },
    },
  },
};

const manualConfig = {
  config: {
    sync: {
      selectedStrategy: 'MANUAL',
    },
  },
};

const activeLocalFsInstance = {
  _key: 'localfs-1',
  type: 'LOCAL_FS',
  isActive: true,
};

describe('shouldRemoveScheduledCrawlingJobOnSyncDisable', () => {
  it('returns true when disabling an active scheduled Local FS connector', () => {
    assert.equal(
      shouldRemoveScheduledCrawlingJobOnSyncDisable(
        activeLocalFsInstance,
        scheduledConfig,
      ),
      true,
    );
  });

  it('returns false for manual Local FS sync', () => {
    assert.equal(
      shouldRemoveScheduledCrawlingJobOnSyncDisable(
        activeLocalFsInstance,
        manualConfig,
      ),
      false,
    );
  });

  it('returns false for non-Local FS scheduled sync', () => {
    assert.equal(
      shouldRemoveScheduledCrawlingJobOnSyncDisable(
        { ...activeLocalFsInstance, type: 'Google Drive' },
        scheduledConfig,
      ),
      false,
    );
  });

  it('returns false when toggling an inactive Local FS connector on', () => {
    assert.equal(
      shouldRemoveScheduledCrawlingJobOnSyncDisable(
        { ...activeLocalFsInstance, isActive: false },
        scheduledConfig,
      ),
      false,
    );
  });
});

describe('buildScheduledCrawlingRemovePath', () => {
  it('builds the crawling-manager remove endpoint with encoded path segments', () => {
    assert.equal(
      buildScheduledCrawlingRemovePath('LOCAL_FS', 'conn/id 1'),
      '/api/v1/crawlingManager/local_fs/conn%2Fid%201/remove',
    );
  });
});
