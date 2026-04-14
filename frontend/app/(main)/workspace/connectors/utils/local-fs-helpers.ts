/**
 * Check if a connector type string identifies a Local FS connector.
 * Matches the backend identifiers: LOCAL_FS, localfs, foldersync, localfilesystem.
 */
export function isLocalFsConnectorType(connectorType: string): boolean {
  const normalized = connectorType.trim().replace(/[_\s]+/g, '').toLowerCase();
  return (
    normalized === 'localfs' ||
    normalized === 'foldersync' ||
    normalized === 'localfilesystem'
  );
}
