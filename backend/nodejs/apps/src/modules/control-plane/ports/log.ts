/** The structured logger the control plane writes to (the app `Logger` satisfies it). */
export interface ControlPlaneLog {
  info(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
  error(message: string, meta?: Record<string, unknown>): void;
}

export const silentControlPlaneLog: ControlPlaneLog = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};
