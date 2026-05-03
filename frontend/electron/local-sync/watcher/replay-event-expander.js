function listFilesUnderPrefix(files, prefix) {
  const normalizedPrefix = String(prefix || '').replace(/\/+$/, '');
  const childPrefix = normalizedPrefix ? `${normalizedPrefix}/` : '';
  return Object.keys(files)
    .filter((relPath) => {
      const entry = files[relPath];
      return entry && !entry.isDirectory && (relPath === normalizedPrefix || relPath.startsWith(childPrefix));
    })
    .sort((a, b) => a.localeCompare(b));
}

/**
 * Expand directory-level events (DIR_DELETED / DIR_MOVED / DIR_RENAMED)
 * into per-file events using the persisted watcher state snapshot.
 * Needed when replaying a failed batch because the backend expects file
 * granularity (the directory may already be gone from disk).
 */
function expandWatchEventsForReplay(events, files) {
  const expanded = [];
  for (const event of events) {
    if (!event.isDirectory) { expanded.push(event); continue; }
    if (event.type === 'DIR_DELETED') {
      for (const relPath of listFilesUnderPrefix(files, event.path)) {
        expanded.push({ type: 'DELETED', path: relPath, timestamp: event.timestamp, isDirectory: false });
      }
      continue;
    }
    if (event.type === 'DIR_MOVED' || event.type === 'DIR_RENAMED') {
      const oldPrefix = String(event.oldPath || '').replace(/\/+$/, '');
      if (!oldPrefix) continue;
      for (const oldRelPath of listFilesUnderPrefix(files, oldPrefix)) {
        const suffix = oldRelPath.slice(oldPrefix.length).replace(/^\/+/, '');
        const newRelPath = [event.path, suffix].filter(Boolean).join('/');
        expanded.push({
          type: event.type === 'DIR_MOVED' ? 'MOVED' : 'RENAMED',
          path: newRelPath, oldPath: oldRelPath,
          timestamp: event.timestamp, isDirectory: false,
        });
      }
    }
  }
  return expanded;
}

module.exports = { expandWatchEventsForReplay };
