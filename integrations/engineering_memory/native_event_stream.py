"""Development adapter; deployment emits the canonical implementation standalone."""
from contextrail.event_stream import SCHEMA, capture_record, list_events, get_event, read_event_range, native_timeline, reindex_timeline
