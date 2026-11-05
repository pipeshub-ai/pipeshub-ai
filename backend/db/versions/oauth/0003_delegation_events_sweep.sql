-- The advisory-lock class of the maintenance job's (db.maintain) retention sweep of delegation events, one lock per org (one lock per org); the other upkeep classes are registered where they are added.
-- Registered once: a class already held by another name is somebody else's and stops the migration instead of being shared.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM kernel.lock_keys WHERE class_id = 7364 AND name <> 'delegation_events_sweep') THEN
    RAISE EXCEPTION 'advisory-lock class 7364 is already registered as %', (SELECT name FROM kernel.lock_keys WHERE class_id = 7364);
  END IF;
  INSERT INTO kernel.lock_keys VALUES (7364, 'delegation_events_sweep', 'one retention sweep of delegation events per org at a time')
    ON CONFLICT (class_id) DO NOTHING;
END $$;
