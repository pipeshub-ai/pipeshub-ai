import { expect } from "chai";
import * as sinon from "sinon";
import type { BackendClient } from "../src/api/backend_client";
import {
  LOCAL_FS_INCLUDE_SUBFOLDERS_KEY,
  LOCAL_FS_SYNC_ROOT_KEY,
} from "../src/api/backend_client";
import {
  applyLocalFsFiltersSync,
  emptyLocalFsFilterCliState,
  fileExtensionsFilterEntry,
  hasAnyFilterChange,
  indexingEntriesFromCliState,
  readAllowedFileExtensionsFromEtcd,
  readEnableManualSyncFromEtcd,
  readIncludeSubfoldersFromEtcd,
  readSyncSettingsFromEtcd,
  syncFilterEntriesFromCliState,
  triFromMutEx,
} from "../src/sync/local_fs_filters";

describe("local_fs_filters", () => {
  describe("readSyncSettingsFromEtcd", () => {
    it("returns empty when sync missing", () => {
      expect(readSyncSettingsFromEtcd({})).to.deep.equal({});
    });

    it("returns empty when sync not object", () => {
      expect(readSyncSettingsFromEtcd({ sync: "x" })).to.deep.equal({});
    });

    it("picks top-level sync keys and nested values", () => {
      const etcd = {
        sync: {
          [LOCAL_FS_SYNC_ROOT_KEY]: "/r",
          [LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]: true,
          selectedStrategy: "s",
          values: {
            [LOCAL_FS_SYNC_ROOT_KEY]: "/nested",
          },
        },
      };
      const out = readSyncSettingsFromEtcd(etcd);
      expect(out[LOCAL_FS_SYNC_ROOT_KEY]).to.equal("/nested");
      expect(out[LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]).to.equal(true);
      expect(out.selectedStrategy).to.equal("s");
    });
  });

  describe("readIncludeSubfoldersFromEtcd", () => {
    it("reads boolean", () => {
      expect(
        readIncludeSubfoldersFromEtcd({
          sync: { [LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]: true },
        })
      ).to.equal(true);
    });

    it("reads string truthy", () => {
      expect(
        readIncludeSubfoldersFromEtcd({
          sync: { [LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]: " YES " },
        })
      ).to.equal(true);
    });

    it("reads wrapped value object", () => {
      expect(
        readIncludeSubfoldersFromEtcd({
          sync: {
            [LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]: { value: "1" },
          },
        })
      ).to.equal(true);
    });

    it("returns undefined when absent", () => {
      expect(readIncludeSubfoldersFromEtcd({ sync: {} })).to.equal(undefined);
    });
  });

  describe("triFromMutEx", () => {
    it("returns true when yes only", () => {
      expect(triFromMutEx(true, false, "x")).to.equal(true);
    });

    it("returns false when no only", () => {
      expect(triFromMutEx(false, true, "x")).to.equal(false);
    });

    it("returns undefined when neither", () => {
      expect(triFromMutEx(false, false, "x")).to.equal(undefined);
    });

    it("throws when both yes and no", () => {
      expect(() => triFromMutEx(true, true, "flag")).to.throw(
        "Conflicting flags for flag"
      );
    });
  });

  describe("fileExtensionsFilterEntry", () => {
    it("normalizes extensions", () => {
      const e = fileExtensionsFilterEntry([" .PDF ", "doc", ""]);
      expect(e).to.deep.equal({
        operator: "in",
        value: ["pdf", "doc"],
        type: "multiselect",
      });
    });
  });

  describe("indexingEntriesFromCliState", () => {
    it("maps set tri-bools to filter entries", () => {
      const out = indexingEntriesFromCliState({
        ...emptyLocalFsFilterCliState,
        manualIndexing: true,
        indexFiles: false,
      });
      expect(out.enable_manual_sync).to.deep.equal({
        operator: "is",
        value: true,
        type: "boolean",
      });
      expect(out.files).to.deep.equal({
        operator: "is",
        value: false,
        type: "boolean",
      });
    });
  });

  describe("syncFilterEntriesFromCliState", () => {
    it("returns empty when no extensions", () => {
      expect(
        syncFilterEntriesFromCliState(emptyLocalFsFilterCliState)
      ).to.deep.equal({});
    });

    it("writes file_extensions when extensions set", () => {
      const out = syncFilterEntriesFromCliState({
        ...emptyLocalFsFilterCliState,
        extensions: ["txt"],
      });
      expect(out.file_extensions).to.deep.equal(
        fileExtensionsFilterEntry(["txt"])
      );
    });

    it("clearExtensions writes empty multiselect", () => {
      const out = syncFilterEntriesFromCliState({
        ...emptyLocalFsFilterCliState,
        clearExtensions: true,
      });
      expect(out.file_extensions).to.deep.equal(
        fileExtensionsFilterEntry([])
      );
    });
  });

  describe("hasAnyFilterChange", () => {
    it("is false for empty state", () => {
      expect(hasAnyFilterChange(emptyLocalFsFilterCliState)).to.equal(false);
    });

    it("detects any field", () => {
      expect(
        hasAnyFilterChange({
          ...emptyLocalFsFilterCliState,
          indexImages: true,
        })
      ).to.equal(true);
      expect(
        hasAnyFilterChange({
          ...emptyLocalFsFilterCliState,
          extensions: [],
        })
      ).to.equal(true);
      expect(
        hasAnyFilterChange({
          ...emptyLocalFsFilterCliState,
          clearExtensions: true,
        })
      ).to.equal(true);
    });
  });

  describe("readAllowedFileExtensionsFromEtcd", () => {
    it("returns undefined when missing", () => {
      expect(readAllowedFileExtensionsFromEtcd({})).to.equal(undefined);
    });

    it("parses multiselect value array", () => {
      const etcd = {
        filters: {
          sync: {
            values: {
              file_extensions: { value: [".PDF", "doc"] },
            },
          },
        },
      };
      expect(readAllowedFileExtensionsFromEtcd(etcd)).to.deep.equal([
        "pdf",
        "doc",
      ]);
    });

    it("returns undefined for empty resulting list", () => {
      const etcd = {
        filters: {
          sync: {
            values: {
              file_extensions: { value: ["", "  "] },
            },
          },
        },
      };
      expect(readAllowedFileExtensionsFromEtcd(etcd)).to.equal(undefined);
    });
  });

  describe("readEnableManualSyncFromEtcd", () => {
    it("reads from indexing.values", () => {
      const etcd = {
        filters: {
          indexing: {
            values: {
              enable_manual_sync: { value: true },
            },
          },
        },
      };
      expect(readEnableManualSyncFromEtcd(etcd)).to.equal(true);
    });

    it("falls back to top-level indexing.enable_manual_sync", () => {
      const etcd = {
        filters: {
          indexing: {
            enable_manual_sync: false,
          },
        },
      };
      expect(readEnableManualSyncFromEtcd(etcd)).to.equal(false);
    });
  });

  describe("applyLocalFsFiltersSync", () => {
    const cid = "connector-1";

    it("no-ops when nothing to apply", async () => {
      const api = {
        getConnectorConfig: sinon.stub(),
        updateConnectorFiltersSync: sinon.stub(),
      };
      await applyLocalFsFiltersSync(api as unknown as BackendClient, cid, {});
      expect(api.getConnectorConfig.called).to.equal(false);
      expect(api.updateConnectorFiltersSync.called).to.equal(false);
    });

    it("sync-only calls update without reading config", async () => {
      const updateConnectorFiltersSync = sinon.stub().resolves();
      const api = {
        getConnectorConfig: sinon.stub(),
        updateConnectorFiltersSync,
      };
      const syncTop = { [LOCAL_FS_SYNC_ROOT_KEY]: "/p" };
      await applyLocalFsFiltersSync(api as unknown as BackendClient, cid, {
        syncTopLevel: syncTop,
      });
      expect(api.getConnectorConfig.called).to.equal(false);
      expect(
        updateConnectorFiltersSync.calledOnceWithExactly(cid, { sync: syncTop })
      ).to.equal(true);
    });

    it("throws when connector active and filter mutation", async () => {
      const api = {
        getConnectorConfig: sinon.stub().resolves({
          isActive: true,
          etcd: {},
          instanceEnvelope: {},
        }),
        updateConnectorFiltersSync: sinon.stub(),
      };
      try {
        await applyLocalFsFiltersSync(api as unknown as BackendClient, cid, {
          syncTopLevel: { [LOCAL_FS_SYNC_ROOT_KEY]: "/p" },
          indexingEntries: { files: { operator: "is", value: true, type: "boolean" } },
        });
        expect.fail("expected throw");
      } catch (e) {
        expect((e as Error).message).to.match(/active/i);
      }
    });

    it("merges filters when inactive", async () => {
      const etcd = {
        filters: {
          indexing: {
            schema: { keep: true },
            values: { existing: 1 },
          },
          sync: {
            schema: { s: 1 },
            values: { old: 2 },
          },
        },
      };
      const updateConnectorFiltersSync = sinon.stub().resolves();
      const api = {
        getConnectorConfig: sinon.stub().resolves({
          isActive: false,
          etcd,
          instanceEnvelope: {},
        }),
        updateConnectorFiltersSync,
      };
      await applyLocalFsFiltersSync(api as unknown as BackendClient, cid, {
        syncTopLevel: { [LOCAL_FS_SYNC_ROOT_KEY]: "/x" },
        indexingEntries: {
          enable_manual_sync: {
            operator: "is",
            value: true,
            type: "boolean",
          },
        },
        syncFilterEntries: {
          file_extensions: fileExtensionsFilterEntry(["md"]),
        },
      });
      expect(updateConnectorFiltersSync.calledOnce).to.equal(true);
      const body = updateConnectorFiltersSync.firstCall.args[1];
      expect(body.sync).to.deep.equal({
        [LOCAL_FS_SYNC_ROOT_KEY]: "/x",
      });
      expect(body.filters.indexing.schema).to.deep.equal({ keep: true });
      expect(body.filters.indexing.values).to.deep.equal({
        existing: 1,
        enable_manual_sync: {
          operator: "is",
          value: true,
          type: "boolean",
        },
      });
      expect(body.filters.sync.values).to.deep.equal({
        old: 2,
        file_extensions: fileExtensionsFilterEntry(["md"]),
      });
    });
  });
});
