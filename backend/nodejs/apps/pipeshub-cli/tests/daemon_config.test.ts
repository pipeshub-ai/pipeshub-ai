import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { expect } from "chai";
import {
  DAEMON_CONFIG_FILENAME,
  daemonConfigComplete,
  emptyDaemonConfig,
  loadDaemonConfig,
  saveDaemonConfig,
  type DaemonConfig,
} from "../src/config/daemon_config";

describe("daemon_config", () => {
  function tmpAuthDir(): string {
    return fs.mkdtempSync(path.join(os.tmpdir(), "pipeshub-daemon-"));
  }

  describe("emptyDaemonConfig", () => {
    it("returns empty strings", () => {
      const c = emptyDaemonConfig();
      expect(c.sync_root).to.equal("");
      expect(c.connector_instance_id).to.equal("");
    });
  });

  describe("daemonConfigComplete", () => {
    it("is false for empty config", () => {
      expect(daemonConfigComplete(emptyDaemonConfig())).to.equal(false);
    });

    it("is false when only sync_root set", () => {
      expect(
        daemonConfigComplete({
          sync_root: "/tmp",
          connector_instance_id: "",
        })
      ).to.equal(false);
    });

    it("is false for whitespace-only fields", () => {
      expect(
        daemonConfigComplete({
          sync_root: "  ",
          connector_instance_id: "  ",
        })
      ).to.equal(false);
    });

    it("is true when both trimmed non-empty", () => {
      expect(
        daemonConfigComplete({
          sync_root: "/data",
          connector_instance_id: "cid-1",
        })
      ).to.equal(true);
    });
  });

  describe("loadDaemonConfig", () => {
    it("returns empty when file missing", () => {
      const dir = tmpAuthDir();
      try {
        const c = loadDaemonConfig(dir);
        expect(c.sync_root).to.equal("");
        expect(c.connector_instance_id).to.equal("");
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("returns empty for invalid JSON", () => {
      const dir = tmpAuthDir();
      try {
        fs.writeFileSync(
          path.join(dir, DAEMON_CONFIG_FILENAME),
          "not json",
          "utf8"
        );
        const c = loadDaemonConfig(dir);
        expect(c).to.deep.equal(emptyDaemonConfig());
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("returns empty fields for JSON array (not a config object)", () => {
      const dir = tmpAuthDir();
      try {
        fs.writeFileSync(
          path.join(dir, DAEMON_CONFIG_FILENAME),
          "[]",
          "utf8"
        );
        const c = loadDaemonConfig(dir);
        expect(c.sync_root).to.equal("");
        expect(c.connector_instance_id).to.equal("");
        expect(c.include_subfolders).to.equal(undefined);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("parses partial fields and include_subfolders", () => {
      const dir = tmpAuthDir();
      try {
        const body = {
          sync_root: "/sync",
          connector_instance_id: "abc",
          include_subfolders: true,
        };
        fs.writeFileSync(
          path.join(dir, DAEMON_CONFIG_FILENAME),
          JSON.stringify(body),
          "utf8"
        );
        const c = loadDaemonConfig(dir);
        expect(c.sync_root).to.equal("/sync");
        expect(c.connector_instance_id).to.equal("abc");
        expect(c.include_subfolders).to.equal(true);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    it("parses string include_subfolders", () => {
      const dir = tmpAuthDir();
      try {
        fs.writeFileSync(
          path.join(dir, DAEMON_CONFIG_FILENAME),
          JSON.stringify({
            sync_root: "/s",
            connector_instance_id: "x",
            include_subfolders: "false",
          }),
          "utf8"
        );
        expect(loadDaemonConfig(dir).include_subfolders).to.equal(false);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });

  describe("saveDaemonConfig", () => {
    it("round-trips via loadDaemonConfig", () => {
      const dir = tmpAuthDir();
      try {
        const cfg: DaemonConfig = {
          sync_root: "/my/root",
          connector_instance_id: "conn-99",
          include_subfolders: true,
        };
        const written = saveDaemonConfig(cfg, dir);
        expect(written).to.equal(path.join(dir, DAEMON_CONFIG_FILENAME));
        const loaded = loadDaemonConfig(dir);
        expect(loaded.sync_root).to.equal("/my/root");
        expect(loaded.connector_instance_id).to.equal("conn-99");
        expect(loaded.include_subfolders).to.equal(true);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  });
});
