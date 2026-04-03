import { expect } from "chai";
import {
  BackendClientError,
  knowledgeBaseRecordDocumentKey,
  unwrapConnectorKnowledgeStats,
} from "../src/api/backend_client";

describe("backend_client helpers", () => {
  describe("knowledgeBaseRecordDocumentKey", () => {
    it("prefers _key over id", () => {
      expect(
        knowledgeBaseRecordDocumentKey({ _key: "a", id: "b" })
      ).to.equal("a");
    });

    it("uses id when _key missing", () => {
      expect(knowledgeBaseRecordDocumentKey({ id: "doc1" })).to.equal("doc1");
    });

    it("returns empty when neither set", () => {
      expect(knowledgeBaseRecordDocumentKey({})).to.equal("");
    });

    it("trims result", () => {
      expect(knowledgeBaseRecordDocumentKey({ _key: "  x  " })).to.equal("x");
    });
  });

  describe("unwrapConnectorKnowledgeStats", () => {
    it("unwraps success envelope", () => {
      const inner = { processed: 1 };
      const out = unwrapConnectorKnowledgeStats({
        success: true,
        data: inner,
      });
      expect(out).to.deep.equal(inner);
    });

    it("returns raw when not wrapped", () => {
      const raw = { foo: 1 };
      expect(unwrapConnectorKnowledgeStats(raw)).to.deep.equal(raw);
    });
  });

  describe("BackendClientError", () => {
    it("sets message and status", () => {
      const e = new BackendClientError("oops", 503);
      expect(e.message).to.equal("oops");
      expect(e.status).to.equal(503);
      expect(e.name).to.equal("BackendClientError");
    });

    it("allows null status", () => {
      const e = new BackendClientError("x");
      expect(e.status).to.equal(null);
    });
  });
});
