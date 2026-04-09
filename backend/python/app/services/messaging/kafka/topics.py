from app.services.messaging.config import Topic

sync_events_topic = Topic.SYNC_EVENTS.value
entity_events_topic = Topic.ENTITY_EVENTS.value
topics = [sync_events_topic, entity_events_topic]
