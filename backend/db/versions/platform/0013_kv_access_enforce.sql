-- Key-value access by prefix is enforced by default: each service role reads and writes only the keys of platform.kv_may (platform_0012's
-- matrix); any other access is refused (the row is not visible, an insert or upsert fails with 42501). This turns enforce on for new and
-- upgraded installs alike. The escape hatch stays: PG_KV_ACCESS=shadow on db.migrate is applied after the upgrade, so an install that
-- sets it keeps shadow (admit and log), and db.migrate warns on every run while the mode is shadow.

UPDATE platform.kv_access SET enforce = true, updated_at = now() WHERE enforce IS DISTINCT FROM true;
