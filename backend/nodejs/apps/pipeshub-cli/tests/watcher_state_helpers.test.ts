import * as path from "path";
import { expect } from "chai";
import {
  connectorFileSegment,
  normalizeRelKey,
  watcherStateFilePath,
} from "../src/sync/watcher_state";

describe("watcher_state helpers", () => {
  describe("connectorFileSegment", () => {
    it("returns unknown for empty", () => {
      expect(connectorFileSegment("")).to.equal("unknown");
      expect(connectorFileSegment("   ")).to.equal("unknown");
    });

    it("replaces unsafe characters with underscore", () => {
      expect(connectorFileSegment("a/b:c")).to.equal("a_b_c");
    });

    it("truncates long ids", () => {
      const long = "x".repeat(300);
      expect(connectorFileSegment(long).length).to.equal(200);
    });
  });

  describe("normalizeRelKey", () => {
    it("returns empty for sync root itself", () => {
      const root = path.join("/data", "sync");
      expect(normalizeRelKey(root, root)).to.equal("");
    });

    it("uses forward slashes in key", () => {
      const root = path.join("/data", "sync");
      const file = path.join(root, "a", "b.txt");
      expect(normalizeRelKey(file, root)).to.equal("a/b.txt");
    });
  });

  describe("watcherStateFilePath", () => {
    it("places file under authDir with connector segment", () => {
      const dir = "/cfg/pipeshub";
      const p = watcherStateFilePath("conn-1", dir);
      expect(p).to.equal(path.join(dir, "watcher_state.conn-1.json"));
    });
  });
});
