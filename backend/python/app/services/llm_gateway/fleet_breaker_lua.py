"""Lua for the fleet breaker (see ``fleet_breaker``). Every script touches the keys of one hash tag only
(``{p:<pk>:<cls>}``), so each is a single-slot script on a cluster, and reads the clock from Redis ``TIME``.

``ARGV`` carries an optional clock override that only tests set (a simulated clock); production passes ''.
"""

_PRELUDE = """
-- Safety state must outlive its longest legitimate life. An open breaker waits up to 60 min for its trial, and a hold
-- is open-ended (the recovery leader refreshes it every 10 s while it lasts), so an open breaker keeps a day, which only
-- garbage-collects one nobody has touched; a closed one keeps an hour.
local OPEN_TTL = 86400
local CLOSED_TTL = 3600
local function now_ms(arg)
  if arg ~= nil and arg ~= '' then return tonumber(arg) end
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local function num(key, field, default)
  local v = redis.call('HGET', key, field)
  if v then return tonumber(v) end
  return default
end
"""

# KEYS: br, waiting, trials
# ARGV: job, now, base_cooldown_ms, lease_ms, stale_ms, claim_after_ms, token
# -> {code, retry_after_ms, token}   code 0 = closed (send), 1 = refuse, 2 = trial granted (send, never charged)
ADMIT = _PRELUDE + """
local br, waiting, trials = KEYS[1], KEYS[2], KEYS[3]
local job = ARGV[1]
local now = now_ms(ARGV[2])
local base_cd, lease, stale, claim_after = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local state = redis.call('HGET', br, 'state')
if (not state) or state == 'closed' then return {0, 0, ''} end
local opened = num(br, 'opened_ms', now)
local cd = num(br, 'cooldown_ms', base_cd)
local due_at = opened + cd
if job ~= '' then
  redis.call('ZADD', waiting, now, job)
  redis.call('HSETNX', trials, job, 0)
  redis.call('EXPIRE', waiting, 86400)
  redis.call('EXPIRE', trials, 86400)
end
-- entries not seen within the window never wake again (a deleted record, a lost message): they age out
local gone = redis.call('ZRANGEBYSCORE', waiting, '-inf', now - stale)
for _, member in ipairs(gone) do
  redis.call('ZREM', waiting, member)
  redis.call('HDEL', trials, member)
end
if now < due_at then return {1, due_at - now, ''} end
local holder = redis.call('HGET', br, 'trial_holder')
local lease_until = num(br, 'trial_until', 0)
if holder and holder ~= '' and lease_until > now then
  return {1, math.min(lease_until - now, 5000), ''}
end
local eligible = false
if (now - due_at) > claim_after then
  eligible = true
else
  local live = redis.call('ZRANGE', waiting, 0, -1)
  local lowest = nil
  for _, member in ipairs(live) do
    local carried = tonumber(redis.call('HGET', trials, member) or '0')
    if lowest == nil or carried < lowest then lowest = carried end
  end
  if job == '' then
    eligible = (lowest == nil)
  else
    eligible = tonumber(redis.call('HGET', trials, job) or '0') <= lowest
  end
end
if not eligible then return {1, 1000, ''} end
redis.call('HSET', br, 'trial_holder', ARGV[7], 'trial_until', now + lease, 'state', 'open')
if job ~= '' then redis.call('HINCRBY', trials, job, 1) end
redis.call('EXPIRE', br, OPEN_TTL)
return {2, 0, ARGV[7]}
"""

# KEYS: br, rate, hold, waiting, trials
# ARGV: now, outcome (ok | ok_unrated | fail | neutral), token ('' unless a trial), was_closed (0/1),
#       base_cooldown_ms, trial_max_ms, min_outcomes, threshold_pct, lease_ms, consecutive_trip, window_trip_min
# -> {state, chargeable, trial_result, released_episode, released_since, released_label, released_cls}
# trial_result: '' (not a trial), closed, reopened, dropped, released
SETTLE = _PRELUDE + """
local br, rate, hold, waiting, trials = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5]
local now = now_ms(ARGV[1])
local outcome, token = ARGV[2], ARGV[3]
local was_closed = ARGV[4] == '1'
local base_cd, trial_max = tonumber(ARGV[5]), tonumber(ARGV[6])
local min_outcomes, threshold = tonumber(ARGV[7]), tonumber(ARGV[8])
local consecutive_trip, window_trip_min = tonumber(ARGV[10]), tonumber(ARGV[11])
local bucket = math.floor(now / 10000)

-- the 5-minute success rate of the key's real traffic, before this outcome joins it
local ok, fail = 0, 0
for i = 0, 29 do
  ok = ok + tonumber(redis.call('HGET', rate, 'o' .. (bucket - i)) or '0')
  fail = fail + tonumber(redis.call('HGET', rate, 'f' .. (bucket - i)) or '0')
end
local total = ok + fail
local chargeable = 0
if outcome == 'fail' and token == '' and was_closed and total >= min_outcomes and ok * 100 >= threshold * total then
  chargeable = 1
end
if outcome == 'ok' or outcome == 'fail' then
  redis.call('HINCRBY', rate, (outcome == 'ok' and 'o' or 'f') .. bucket, 1)
  -- buckets older than the 5-minute window go; with gaps in the traffic the two just behind it are not enough, so a
  -- hash that has grown past the 30 live buckets is swept
  if redis.call('HLEN', rate) > 64 then
    for _, field in ipairs(redis.call('HKEYS', rate)) do
      if tonumber(string.sub(field, 2)) < bucket - 29 then redis.call('HDEL', rate, field) end
    end
  end
  redis.call('EXPIRE', rate, 600)
end

local result = ''
local rel_episode, rel_since, rel_label, rel_cls = '', '', '', ''
local state = redis.call('HGET', br, 'state') or 'closed'
if token ~= '' then
  local holder = redis.call('HGET', br, 'trial_holder')
  if holder ~= token then
    result = 'dropped'
  elseif outcome == 'neutral' then
    redis.call('HSET', br, 'trial_holder', '', 'trial_until', 0)
    result = 'released'
  elseif outcome == 'fail' then
    local cd = num(br, 'cooldown_ms', base_cd)
    redis.call('HSET', br, 'state', 'open', 'opened_ms', now, 'cooldown_ms', math.min(cd * 2, trial_max),
      'trial_holder', '', 'trial_until', 0)
    state = 'open'
    result = 'reopened'
  else
    redis.call('HSET', br, 'state', 'closed', 'consecutive', 0, 'window', '', 'cooldown_ms', base_cd,
      'trial_holder', '', 'trial_until', 0)
    state = 'closed'
    result = 'closed'
    -- the outage is over: the jobs that waited through it, and the trials they carried, are not a later outage's fairness
    redis.call('DEL', waiting, trials)
    if redis.call('EXISTS', hold) == 1 then
      rel_episode = redis.call('HGET', hold, 'episode') or ''
      rel_since = redis.call('HGET', hold, 'since_ms') or ''
      rel_label = redis.call('HGET', hold, 'label') or ''
      rel_cls = redis.call('HGET', hold, 'cls') or ''
      redis.call('DEL', hold)
    end
  end
elseif state == 'closed' and (outcome == 'ok' or outcome == 'ok_unrated' or outcome == 'fail') then
  local window = redis.call('HGET', br, 'window') or ''
  local consecutive = num(br, 'consecutive', 0)
  if outcome == 'fail' then
    window = window .. '1'
    consecutive = consecutive + 1
  else
    window = window .. '0'
    consecutive = 0
  end
  if string.len(window) > 20 then window = string.sub(window, string.len(window) - 19) end
  redis.call('HSET', br, 'window', window, 'consecutive', consecutive)
  if outcome == 'fail' then
    local fails = 0
    for i = 1, string.len(window) do
      if string.sub(window, i, i) == '1' then fails = fails + 1 end
    end
    if consecutive >= consecutive_trip or (string.len(window) >= window_trip_min and fails * 2 >= string.len(window)) then
      redis.call('HSET', br, 'state', 'open', 'opened_ms', now, 'cooldown_ms', base_cd, 'trial_holder', '', 'trial_until', 0)
      state = 'open'
    end
  end
end
if state == 'open' then redis.call('EXPIRE', br, OPEN_TTL) else redis.call('EXPIRE', br, CLOSED_TTL) end
return {state, chargeable, result, rel_episode, rel_since, rel_label, rel_cls}
"""

# KEYS: br, trial lease renewal
# ARGV: now, token, lease_ms -> 1 when the holder still holds the lease (and it was extended), else 0
RENEW = _PRELUDE + """
local br = KEYS[1]
local now = now_ms(ARGV[1])
if redis.call('HGET', br, 'trial_holder') ~= ARGV[2] then return 0 end
redis.call('HSET', br, 'trial_until', now + tonumber(ARGV[3]))
return 1
"""

# KEYS: capped, counted, br, hold, owed
# ARGV: now, job, window_ms, hold_jobs, hold_cooldown_ms, episode, reason, label, cls, owed_ttl_s, org
# The hold hash names the org to notify, so the recovery leader can rebuild a hold it lost track of from Redis alone.
# ``owed`` maps each job owed a redrive to the hold episode it is owed for, so its charge reset happens once per episode.
# -> {already, distinct, newly_held, episode, member...}
# A job counts once per revision (``counted``). When the hold rule fires the window is cleared, so the same
# set cannot trigger another hold; a counted job that reaches the cap again is refused (already = 1).
# While the key is on hold, a job reaching the cap (its send was in flight when the hold began) counts as
# the key's: distinct is reported as ``hold_jobs`` so the caller redrives it.
CAPPED = _PRELUDE + """
local capped, counted, br, hold, owed = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5]
local now = now_ms(ARGV[1])
local job, window, hold_jobs = ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4])
local owed_ttl = tonumber(ARGV[10])
redis.call('ZREMRANGEBYSCORE', capped, '-inf', now - window)
if redis.call('EXISTS', hold) == 1 then
  -- The key is on hold: a send that was in flight when it began, settling now, is the key's fault, not a
  -- document's. The job is redriven like the ones the hold absorbed, and owed that until it is done.
  redis.call('HSETNX', counted, job, now)
  redis.call('EXPIRE', counted, 8 * 86400)
  redis.call('HSET', owed, job, redis.call('HGET', hold, 'episode') or '')
  redis.call('EXPIRE', owed, owed_ttl)
  return {0, hold_jobs, 0, redis.call('HGET', hold, 'episode') or ''}
end
if redis.call('HSETNX', counted, job, now) == 0 then
  return {1, redis.call('ZCARD', capped), 0, redis.call('HGET', hold, 'episode') or ''}
end
redis.call('EXPIRE', counted, 8 * 86400)
redis.call('ZADD', capped, now, job)
redis.call('EXPIRE', capped, 9000)
local distinct = redis.call('ZCARD', capped)
local episode = redis.call('HGET', hold, 'episode') or ''
local newly = 0
local out = {0, distinct, 0, episode}
if distinct >= hold_jobs then
  if redis.call('EXISTS', hold) == 0 then
    episode = ARGV[6]
    redis.call('HSET', hold, 'since_ms', now, 'reason', ARGV[7], 'episode', episode, 'label', ARGV[8], 'cls', ARGV[9],
      'org', ARGV[11])
    newly = 1
  end
  redis.call('HSET', br, 'state', 'open', 'opened_ms', now, 'cooldown_ms', tonumber(ARGV[5]),
    'trial_holder', '', 'trial_until', 0)
  redis.call('EXPIRE', br, OPEN_TTL)
  out[3] = newly
  out[4] = episode
  -- Every capped job of the window, this one included, is owed a redrive until it is done: written here, with the
  -- hold, so no failure after this script can lose one.
  for _, member in ipairs(redis.call('ZRANGE', capped, 0, -1)) do
    table.insert(out, member)
    redis.call('HSET', owed, member, episode)
  end
  redis.call('EXPIRE', owed, owed_ttl)
  redis.call('DEL', capped)
end
return out
"""

# KEYS: ledger, last. ARGV: now, held (0/1), since_ms (when the hold began; '' or 0 when unknown) -> the ledger in ms
# The credited interval starts at the later of the last advance and the hold's start, so time before the hold began
# (the last advance of an earlier hold) is never credited, however late the hold was first ticked.
ADVANCE_LEDGER = _PRELUDE + """
local ledger, last = KEYS[1], KEYS[2]
local now = now_ms(ARGV[1])
local previous = tonumber(redis.call('GET', last) or tostring(now))
local since = tonumber(ARGV[3] or '') or 0
if since > previous then previous = since end
if ARGV[2] == '1' and now > previous then redis.call('INCRBY', ledger, now - previous) end
redis.call('SET', last, now)
return tonumber(redis.call('GET', ledger) or '0')
"""

# KEYS: br, hold. ARGV: -
# The leader's keep-alive of a hold. -> 1 held and kept, 0 the breaker is no longer open so the hold is released
# (an explicit release: returns its episode in the second item), -1 no hold.
HOLD_KEEP = _PRELUDE + """
local br, hold = KEYS[1], KEYS[2]
if redis.call('EXISTS', hold) == 0 then return {-1, ''} end
if redis.call('HGET', br, 'state') == 'open' then
  redis.call('EXPIRE', br, OPEN_TTL)
  return {1, ''}
end
local episode = redis.call('HGET', hold, 'episode') or ''
redis.call('DEL', hold)
return {0, episode}
"""
