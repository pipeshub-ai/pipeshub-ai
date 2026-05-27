/**
 * Detect if the app is running inside Electron.
 * Checks both the preload-exposed flag and the user-agent string.
 */
export function isElectron(): boolean {
  if (typeof window === 'undefined') return false;
  const electronAPI = (window as unknown as { electronAPI?: { isElectron?: boolean } })
    .electronAPI;
  return !!electronAPI?.isElectron || navigator.userAgent.toLowerCase().includes('electron');
}
