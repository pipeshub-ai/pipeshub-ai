/**
 * OS-native credential storage via `keytar`:
 *   - macOS:   Keychain
 *   - Windows: Credential Manager
 *   - Linux:   libsecret (GNOME Keyring / KWallet)
 */

export interface KeytarModule {
  setPassword(service: string, account: string, password: string): Promise<void>;
  getPassword(service: string, account: string): Promise<string | null>;
  deletePassword(service: string, account: string): Promise<boolean>;
  findPassword(service: string): Promise<string | null>;
}

export class KeychainBackend {
  private keytar: KeytarModule | null = null;

  async isAvailable(): Promise<boolean> {
    try {
      const keytarModule = await import("keytar");
      this.keytar =
        (keytarModule as unknown as { default: KeytarModule }).default ??
        (keytarModule as unknown as KeytarModule);
      await this.keytar.findPassword("__pipeshub_keychain_probe__");
      return true;
    } catch {
      this.keytar = null;
      return false;
    }
  }

  async set(service: string, account: string, password: string): Promise<void> {
    this.ensureLoaded();
    await this.keytar!.setPassword(service, account, password);
  }

  async get(service: string, account: string): Promise<string | null> {
    this.ensureLoaded();
    return await this.keytar!.getPassword(service, account);
  }

  async delete(service: string, account: string): Promise<boolean> {
    this.ensureLoaded();
    return await this.keytar!.deletePassword(service, account);
  }

  private ensureLoaded(): void {
    if (!this.keytar) {
      throw new Error(
        "Keychain backend is not available. Call isAvailable() first."
      );
    }
  }
}
