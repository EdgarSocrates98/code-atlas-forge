-- Fixture: Athena CTAS + INSERT — reuse total do adapter sql.
CREATE EXTERNAL TABLE staging.raw_events (
    event_id string,
    payload string
)
LOCATION 's3://events-bucket/raw/';

INSERT INTO staging.daily_events
SELECT event_id, payload FROM staging.raw_events;
