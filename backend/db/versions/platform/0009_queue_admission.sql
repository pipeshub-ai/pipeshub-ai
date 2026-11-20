-- A consumer that opened a delivery's message (signature and age verified) writes that down, so the age check is not applied again
-- to a message it already admitted (a hand-back that waits through an outage, a handler that outlives the envelope, a claim that
-- lapses). The proof is not the row: admission_mac is a keyed MAC over the message's identity, the group, the message id and
-- admission_ms, made with the envelope key the database does not hold. A row written or edited without the key is not admitted.
-- NULL columns: never admitted. Metadata-only change; the migrate role's lock_timeout makes it retry if the queue is busy.
ALTER TABLE platform.queue_deliveries ADD COLUMN admission_ms bigint;     -- epoch milliseconds of the admission (consumer clock)
ALTER TABLE platform.queue_deliveries ADD COLUMN admission_mac text;
