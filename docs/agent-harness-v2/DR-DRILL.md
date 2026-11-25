# Agent harness: disaster recovery drill

The procedure G-47 and C20.48 ask for. It tests the RPO and RTO targets in [SLO.md](./SLO.md), "Availability,
error budget, RPO and RTO". Run it on a staging deployment, never on production. Record each run in "Drill
record" at the end of this page.

**Status:** rehearsed on a lane, not yet run on staging. `deployment/docker-compose/tests/airgap_install_smoke.sh`
with `AIRGAP_DR=1` runs the Compose procedure below on an install made by `install.sh` (R25-OPS, 2026-11-11): it
backs up, removes the MongoDB volume and the blob volume, restores, and checks that a conversation and an engine
job from before the backup are back, that the conversation sent after it is gone, that every admin dependency reads
ok and that new turns and jobs work. The rehearsal found that `--drop` on the restore breaks it (see step 3). A
lane has neither production's data volume nor its backup schedule, so its RPO and RTO are not production's: the
staging run below still decides the targets.

What the drill proves:

1. A backup taken the documented way restores to a deployment where people can read their conversations, open
   their artifacts and keep working.
2. A run that was in flight when the disaster struck resumes after the restore, and its outside effects run at most
   once.
3. How long the restore took (the measured RTO) and how much was lost (the measured RPO).

The automated drill for one session, `backend/python/tests/unit/harness/engines/test_p9_backup_drill.py` (BR-3),
already covers points 1 and 2 for a single crashed engine run in a fresh database and object store. This drill is
the same thing for the whole deployment, on real infrastructure.

## Before you start

- A staging deployment on the same release, topology and storage provider as production (Compose, or Helm with
  `values-cloud.yaml` or `values-eks.yaml`). Helm's base `values.yaml` keeps the harness off (see SLO.md).
- The staging `.env` or Helm values, including `SECRET_KEY`. A restore must use the same key: the database holds
  the org's data keys wrapped with it, never in clear. A backup taken before a `SECRET_KEY` rotation needs the key
  that was current when it was taken.
- A test org with:
  - a finished conversation with an artifact;
  - a conversation with a run waiting on an approval;
  - an engine run in the middle of a long task, if engines are on;
  - one schedule.
- The engine canary set up (`scripts/engines/canary.py`, guides/operator-guide.md "Canary"), passing.
- A clock. Note the time at every step.

Write down, before the disaster:

- [ ] `GET /api/v1/health` shows `agentHarness.status` `available`.
- [ ] As an org admin, `GET /api/v1/agent-harness/admin/dependencies` shows mongo, kv, broker and blob ok.
- [ ] The canary passes.
- [ ] The document count of `agentRuns`, `sessionEvents` and `conversations`, and the newest `sessionEvents` time.

## 1. Take the backup

Take it the way production does. On EKS that is the AWS Backup plan's snapshot; start an on-demand backup of the
same volumes, or use the last nightly one and note its time. On Compose, and on Helm without EKS:

**Compose**

```bash
cd deployment/docker-compose
docker compose -p pipeshub-ai exec -T mongodb mongodump --archive --gzip --oplog \
  --username admin --password '<MONGO_PASSWORD from .env>' --authenticationDatabase admin \
  > backups/drill-$(date +%Y%m%d-%H%M).archive.gz
```

Then copy, with the app stopped so the copy is consistent with the dump
(`docker compose -p pipeshub-ai stop pipeshub-ai`, and `start` it after):

- the blob store: the `pipeshub_root_local` volume for local storage, or the S3 or Azure prefix. For the volume:
  `docker run --rm --network none --entrypoint tar -v pipeshub-ai_pipeshub_root_local:/src:ro pipeshubai/pipeshub-ai:<tag> -C /src -czf - . > backups/blobs-<timestamp>.tgz`;
- the `mongodb_replset_key` volume, or `MONGO_REPLICA_SET_KEY` from your secret store;
- `.env`.

**Helm** (the commands from the chart README)

```bash
kubectl exec -n $NS $REL-mongodb-0 -c mongodb -- mongodump --quiet --archive --gzip --oplog \
  -u root -p "$PW" --authenticationDatabase admin > drill.archive.gz
```

Copy the S3 or Azure prefix the same way (or rely on bucket versioning on EKS).

Note the backup's time: **T_backup**.

After the backup, make one more change in the test org: send one more message. The restore should lose it. That
is how the drill measures the RPO.

## 2. Make the disaster

Note the time: **T_disaster**. Then remove the data, as a real loss would:

- **Compose:** `docker compose -p pipeshub-ai down`, then remove the `mongodb_data` volume and empty the blob store.
- **Helm:** delete the Mongo StatefulSet's PVCs and the blob prefix, or restore into a new namespace.

Leave Redis as it is. Redis holds no durable harness state; losing it is a separate, smaller drill (see "Other
drills").

## 3. Restore

Start the decision clock: the time you decide to restore is **T_decide**.

**Compose**

```bash
docker compose -p pipeshub-ai up -d mongodb
docker compose -p pipeshub-ai exec -T mongodb mongorestore --archive --gzip --oplogReplay \
  --username admin --password '<MONGO_PASSWORD from .env>' --authenticationDatabase admin \
  < backups/drill-<timestamp>.archive.gz
```

No `--drop`: the database is new, so there is nothing to drop, and with `--drop` the restore replaces the admin
user it is signed in as with the backup's copy (same name and password, another id), after which MongoDB refuses
its oplog replay (`applyOps requires authentication`; the R25-OPS rehearsal hit it). `--oplogReplay` cannot be
combined with `--nsExclude`, so leaving the users out is not an option either.

Put the blob copy back into a volume Compose made, so it is the project's own:
`docker compose -p pipeshub-ai create` makes every container and volume without starting them (run it before
`up -d mongodb` above, or after; it leaves the running database alone), then
`docker run --rm -i --network none --entrypoint tar -v pipeshub-ai_pipeshub_root_local:/dst pipeshubai/pipeshub-ai:<tag> -C /dst -xzf - < backups/blobs-<timestamp>.tgz`.
Then start the rest: `docker compose -p pipeshub-ai up -d`. If the install uses `PIPESHUB_COMPOSE_OVERRIDES`,
pass the same `-f` files to every command here (the installer prints them).

**Helm**

```bash
kubectl wait -n $NS --for=condition=Ready pod/$REL-mongodb-0 --timeout=300s
kubectl exec -i -n $NS $REL-mongodb-0 -c mongodb -- mongorestore --quiet --archive --gzip --oplogReplay \
  -u root -p "$PW" --authenticationDatabase admin < drill.archive.gz
kubectl rollout restart -n $NS deployment/$REL-pipeshub-ai
```

Put the blob prefix back.

When Node starts, its migrations recreate the indexes. Note when `GET /api/v1/health` shows `agentHarness.status`
`available` again: **T_ready**.

## 4. Check

- [ ] As an org admin, `GET /api/v1/agent-harness/admin/dependencies` shows every dependency ok.
- [ ] The canary passes.
- [ ] The finished conversation opens, with its activity, and its artifact downloads.
- [ ] The run that waited on an approval still waits, and allowing it lets it finish.
- [ ] The engine run that was in flight is requeued by the sweeper once its lease lapses (SLO #6: within about
      65 s), and carries on from its last checkpoint.
- [ ] No outside effect ran twice. Check the systems the test runs wrote to.
- [ ] The schedule is listed and fires at its next time.
- [ ] The document counts match the ones you wrote down, minus what came after **T_backup**.
- [ ] The message sent after the backup is missing. Nothing older is.

Note when every check has passed: **T_recovered**.

## 5. Work out the result

- **Measured RTO** = T_recovered − T_decide. Target: ≤ 4 h.
- **Measured RPO** = T_disaster − T_backup. Target: ≤ 24 h. This is set by how often you back up, so also record
  the backup schedule the deployment really runs.

If either is over target, or any check failed, open an issue with the step and what you saw. Fix the cause, then
run the drill again. Change the target in SLO.md only with a reason, never to make a failed drill pass.

## Other drills

Smaller drills, each covered by the fault suite in CI, which can also be run by hand on staging:

| Drill | How | What should happen | Suite |
|---|---|---|---|
| A Mongo member goes down (three-member set) | delete the primary's pod | a new primary within seconds; runs carry on; nothing lost | `integration-tests/harness/fault/test_degraded.py` (`ix_dd_1`) |
| Redis goes down | stop Redis mid-run | runs carry on; streaming falls back to Mongo; **Stop** still works within 20 s | `test_degraded.py` (`ix_dd_3a`–`3d`) |
| The sandbox broker goes down | stop it mid-run | engine runs wait, then resume when it is back | `test_degraded.py` (`ix_dd_4`) |
| One session moved to another deployment | the session backup CLI | the conversation opens there and an interrupted run resumes | guides/operator-guide.md "Session backup and restore"; BR-3 |
| A backup from before a `SECRET_KEY` rotation | restore it with the **old** key as `SECRET_KEY`, then rotate again (guides/runbooks.md "Rotating `SECRET_KEY`") | everything opens under the old key; after `rotate`, `retire` exits 0 and the new key alone reads it | `secret-key-rotation.mongo.itest.ts` (KI-1..KI-3) |

## Drill record

| Date | Deployment | Backup used | Measured RPO | Measured RTO | Checks failed | Issues | Run by |
|---|---|---|---|---|---|---|---|
| 2026-11-11 | Rehearsal, not staging: Compose `slim` from `install.sh --no-pull` on the development host's Docker lane, no route out (image built from `b54544ef4f` + R25-OPS) | `mongodump --oplog` (360 KB) and the blob volume (196 KB), taken by the script | 55 s (the scripted gap between backup and disaster) | 96 s (harness available after 82 s) | none (8 of 8) | `--drop` broke the restore: fixed in step 3 | R25-OPS |
| | | | | | | | |
