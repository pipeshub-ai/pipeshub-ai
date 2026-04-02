import { expect } from "chai";
import * as sinon from "sinon";
import {
  machineUserSalt,
  pythonPlatformSystem,
  type MachineIdentity,
} from "../src/auth/token_store";

describe("token_store", () => {
  afterEach(() => {
    sinon.restore();
  });

  describe("machineUserSalt", () => {
    it("is deterministic for fixed identity", () => {
      const id: MachineIdentity = {
        hostname: "h",
        system: "Linux",
        sysPlatform: "linux",
        home: "/home/u",
      };
      const a = machineUserSalt(id);
      const b = machineUserSalt(id);
      expect(a.equals(b)).to.equal(true);
      expect(a.length).to.equal(32);
    });

    it("changes when hostname changes", () => {
      const base: MachineIdentity = {
        hostname: "h1",
        system: "Linux",
        sysPlatform: "linux",
        home: "/home/u",
      };
      const other = { ...base, hostname: "h2" };
      expect(machineUserSalt(base).equals(machineUserSalt(other))).to.equal(
        false
      );
    });
  });

  describe("pythonPlatformSystem", () => {
    it("maps win32 to Windows", () => {
      sinon.stub(process, "platform").value("win32");
      expect(pythonPlatformSystem()).to.equal("Windows");
    });

    it("maps darwin to Darwin", () => {
      sinon.stub(process, "platform").value("darwin");
      expect(pythonPlatformSystem()).to.equal("Darwin");
    });

    it("maps linux to Linux", () => {
      sinon.stub(process, "platform").value("linux");
      expect(pythonPlatformSystem()).to.equal("Linux");
    });
  });
});
