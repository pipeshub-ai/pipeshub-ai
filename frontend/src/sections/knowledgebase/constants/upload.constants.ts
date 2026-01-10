/**
 * Constants for upload notification system
 */

/**
 * Maximum time to wait for socket events before applying fallback (milliseconds)
 * This is a safety mechanism in case socket events are delayed or lost
 */
export const SOCKET_EVENT_TIMEOUT_MS = 10000; // 10 seconds

/**
 * Storage key for persisting active uploads across page refreshes
 */
export const ACTIVE_UPLOADS_STORAGE_KEY = 'kb_active_uploads';


