import { expect } from "chai";
import {
  getBackendBaseUrl,
  hasBackendUrlFromEnv,
} from "../src/auth/backend_url";

describe("backend_url", () => {
  const key = "PIPESHUB_BACKEND_URL";
  let prev: string | undefined;

  beforeEach(() => {
    prev = process.env[key];
    delete process.env[key];
  });

  afterEach(() => {
    if (prev === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = prev;
    }
  });

  describe("getBackendBaseUrl", () => {
    it("defaults to localhost:3000 when unset", () => {
      expect(getBackendBaseUrl()).to.equal("http://localhost:3000");
    });

    it("trims whitespace", () => {
      process.env[key] = "  https://api.example.com  ";
      expect(getBackendBaseUrl()).to.equal("https://api.example.com");
    });

    it("strips trailing slash", () => {
      process.env[key] = "https://api.example.com/";
      expect(getBackendBaseUrl()).to.equal("https://api.example.com");
    });
  });

  describe("hasBackendUrlFromEnv", () => {
    it("is false when unset", () => {
      expect(hasBackendUrlFromEnv()).to.equal(false);
    });

    it("is false when only whitespace", () => {
      process.env[key] = "   ";
      expect(hasBackendUrlFromEnv()).to.equal(false);
    });

    it("is true when set to non-empty", () => {
      process.env[key] = "http://localhost:3000";
      expect(hasBackendUrlFromEnv()).to.equal(true);
    });
  });
});
