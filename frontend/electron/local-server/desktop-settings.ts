import * as fs from 'fs';
import * as path from 'path';

export type DesktopMode = 'local' | 'remote';

export interface DesktopSettings {
  /** null until the first-run choice has been made. */
  mode: DesktopMode | null;
  /** Local mode: closing the window hides it to the tray and PipesHub keeps running. */
  keepRunningInTray: boolean;
}

const DEFAULTS: DesktopSettings = { mode: null, keepRunningInTray: false };

/** Small JSON file in the app's user-data folder; a corrupt file reads as the defaults. */
export class DesktopSettingsStore {
  private value: DesktopSettings;

  constructor(private readonly file: string) {
    this.value = DesktopSettingsStore.read(file);
  }

  get(): DesktopSettings {
    return { ...this.value };
  }

  update(patch: Partial<DesktopSettings>): DesktopSettings {
    const next = { ...this.value };
    if (patch.mode === null || patch.mode === 'local' || patch.mode === 'remote') next.mode = patch.mode;
    if (typeof patch.keepRunningInTray === 'boolean') next.keepRunningInTray = patch.keepRunningInTray;
    this.value = next;
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2));
    fs.renameSync(tmp, this.file);
    return this.get();
  }

  private static read(file: string): DesktopSettings {
    try {
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      return {
        mode: raw.mode === 'local' || raw.mode === 'remote' ? raw.mode : null,
        keepRunningInTray: raw.keepRunningInTray === true,
      };
    } catch {
      return { ...DEFAULTS };
    }
  }
}
