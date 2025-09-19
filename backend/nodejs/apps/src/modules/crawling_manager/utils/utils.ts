import {
  EventType, Event,
  ReindexAllRecordEvent,
} from '../../knowledge_base/services/sync_events.service';

export const constructSyncConnectorEvent = (
  orgId: string,
  connector: string,
) : Event => {
  const eventTypeMap: Record<string, EventType> = {
    'drive': EventType.SyncDriveEvent,
    'gmail': EventType.SyncGmailEvent,
    'onedrive': EventType.SyncOneDriveEvent,
    'sharepointonline': EventType.SyncSharePointOnlineEvent,
  };

  const eventType = eventTypeMap[connector.replace(' ', '').toLowerCase()] || EventType.ReindexAllRecordEvent;

  const payload = {
    orgId: orgId,
    origin: 'CONNECTOR',
    connector: connector,
    createdAtTimestamp: Date.now().toString(),
    updatedAtTimestamp: Date.now().toString(),
    sourceCreatedAtTimestamp: Date.now().toString(),
  } as ReindexAllRecordEvent;

  const event : Event = {
    eventType: eventType,
    timestamp: Date.now(),
    payload: payload,
  };

  return event;
};
