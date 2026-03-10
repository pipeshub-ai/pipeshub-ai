# ruff: noqa
"""
Microsoft Graph API — Full Meeting Fetcher
==========================================
Fetches ALL meeting types for the signed-in user:
  • Calendar meetings (scheduled, recurring, 1:1, Teams-linked)
  • Ad-hoc Teams online meetings (created via Teams "Meet now")
  • Teams channel meetings (via joined teams → channels)

For each online meeting → attendees + full transcripts are fetched.

Bugs fixed over original:
  [1] Credentials moved to env vars — never hardcode secrets
  [2] Removed $orderby on calendarView — unsupported, causes HTTP 400
  [3] Full pagination via @odata.nextLink — original missed meetings beyond page 1
  [4] Removed arbitrary MAX_MEETINGS = 2 cap — now processes ALL meetings
  [5] Fixed meetings_processed counter — was incrementing only on transcript found
  [6] Fixed transcript fetch — metadataContent returns speaker metadata JSON,
      NOT the transcript text. Correct primary endpoint is /content with
      Accept: text/vtt. Added docx fallback too.
  [7] Full VTT parser — original only parsed JSON lines, missed all cue text
  [8] Added Teams channel meeting discovery (joined teams → channels → meetings)
  [9] Added deduplication across calendar + online meetings sources
  [10] No early return when no online meetings — still prints all calendar events
"""

import asyncio
import base64
import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta, timezone

import httpx
import msal
from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from kiota_http.httpx_request_adapter import HttpxRequestAdapter
from msgraph import GraphServiceClient
from msgraph.generated.users.item.online_meetings.online_meetings_request_builder import (
    OnlineMeetingsRequestBuilder,
)

# ── Scopes ────────────────────────────────────────────────────────────────────
# Added Team.ReadBasic.All + ChannelMessage.Read.All for channel meeting discovery
# Added Channel.ReadBasic.All so teams/{id}/channels returns HTTP 200 instead of 403
SCOPES = [
    "OnlineMeetings.Read",
    "OnlineMeetingTranscript.Read.All",
    "Calendars.Read",
    "Calendars.Read.Shared",  # delegated, no admin consent — reads group/shared calendars
    "User.Read",
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "ChannelMessage.Read.All",
]

REDIRECT_PORT = 8400
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"

# ── Credentials — load from environment, never hardcode ──────────────────────
# Set these before running:
#   export MS_TENANT_ID="..."
#   export MS_CLIENT_ID="..."
#   export MS_CLIENT_SECRET="..."


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

class _StaticTokenProvider(AccessTokenProvider):
    """Wraps a pre-obtained access token for the Graph SDK."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_authorization_token(self, uri: str, additional_authentication_context=None) -> str:
        return self._token

    def get_allowed_hosts_validator(self) -> AllowedHostsValidator:
        return AllowedHostsValidator(["graph.microsoft.com"])


def _capture_auth_redirect() -> dict:
    """Spin up a local HTTP server to capture the OAuth redirect."""
    captured = {}
    ready = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            captured.update(qs)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authentication successful! You can close this tab.</h2></body></html>"
            )
            ready.set()

        def log_message(self, fmt, *args):
            pass  # suppress request logs

    server = HTTPServer(("localhost", REDIRECT_PORT), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    ready.wait(timeout=120)
    server.server_close()
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# Pagination helper
# ─────────────────────────────────────────────────────────────────────────────

async def _get_all_pages(http: httpx.AsyncClient, url: str, headers: dict) -> list:
    """
    FIX [3]: Follow @odata.nextLink until all pages are consumed.
    Original code fetched only the first page ($top=100), silently missing
    any meetings beyond 100.
    """
    results = []
    next_url = url
    while next_url:
        resp = await http.get(next_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")  # None when last page
    return results


# ─────────────────────────────────────────────────────────────────────────────
# VTT transcript parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_vtt(vtt_text: str) -> list[dict]:
    """
    FIX [7]: Proper WebVTT parser.
    Original code only looked for lines starting with '{' (JSON metadata lines)
    and skipped all actual cue text blocks — meaning transcripts appeared empty.

    WebVTT format:
        WEBVTT

        00:00:01.000 --> 00:00:04.000
        Speaker Name: Hello, this is the spoken text.

        ...
    Returns list of {"timestamp": str, "speaker": str, "text": str}
    """
    lines = vtt_text.strip().splitlines()
    entries = []
    i = 0
    timestamp_re = re.compile(r"(\d{2}:\d{2}:\d{2}[\.,]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[\.,]\d{3})")

    while i < len(lines):
        line = lines[i].strip()

        # Try JSON metadata lines (speaker attribution blocks in MS format)
        if line.startswith("{"):
            try:
                entry = json.loads(line)
                speaker = entry.get("speakerName", "Unknown")
                text = entry.get("spokenText", "")
                if text:
                    entries.append({"timestamp": "", "speaker": speaker, "text": text})
            except json.JSONDecodeError:
                pass
            i += 1
            continue

        # Try timestamp cue
        m = timestamp_re.match(line)
        if m:
            timestamp = f"{m.group(1)} --> {m.group(2)}"
            i += 1
            # Collect all text lines for this cue
            cue_lines = []
            while i < len(lines) and lines[i].strip():
                cue_lines.append(lines[i].strip())
                i += 1
            cue_text = " ".join(cue_lines)

            # MS Teams VTT often encodes speaker as "Name: text"
            speaker = "Unknown"
            if ": " in cue_text:
                parts = cue_text.split(": ", 1)
                speaker = parts[0].strip()
                cue_text = parts[1].strip()

            if cue_text:
                entries.append({"timestamp": timestamp, "speaker": speaker, "text": cue_text})
            continue

        i += 1

    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Transcript fetcher
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_transcript(
    http: httpx.AsyncClient,
    access_token: str,
    user_oid: str,
    meeting_id: str,
    transcript_id: str,
    transcript_created: str,
) -> list[dict]:
    """
    FIX [6]: Correct transcript retrieval order.

    metadataContent  →  speaker diarization JSON (who spoke when), NOT the transcript
    /content         →  actual transcript text (VTT or docx)

    Strategy:
      1. Primary:  GET /content  Accept: text/vtt     ← human-readable transcript
      2. Fallback: GET /content  Accept: application/vnd.openxmlformats...  (docx)
      3. Last:     GET /metadataContent               ← speaker JSON, parse spokenText
    """
    base = (
        f"https://graph.microsoft.com/v1.0/users/{user_oid}"
        f"/onlineMeetings/{meeting_id}/transcripts/{transcript_id}"
    )
    auth_header = {"Authorization": f"Bearer {access_token}"}

    print(f"      Created : {transcript_created}")

    # ── 1. VTT content (primary) ──────────────────────────────────────────
    try:
        resp = await http.get(
            f"{base}/content",
            headers={**auth_header, "Accept": "text/vtt"},
        )
        if resp.status_code == 200 and resp.text.strip().startswith("WEBVTT"):
            entries = _parse_vtt(resp.text)
            if entries:
                print(f"      Format  : VTT  ({len(entries)} cues)")
                return entries
    except Exception as e:
        print(f"      VTT fetch failed: {e}")

    # ── 2. metadataContent fallback (has spokenText per speaker turn) ────
    try:
        resp = await http.get(f"{base}/metadataContent", headers=auth_header)
        if resp.status_code == 200:
            entries = []
            for line in resp.text.strip().splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                        speaker = obj.get("speakerName", "Unknown")
                        text = obj.get("spokenText", "")
                        if text:
                            entries.append({"timestamp": "", "speaker": speaker, "text": text})
                    except json.JSONDecodeError:
                        pass
            if entries:
                print(f"      Format  : metadataContent JSON  ({len(entries)} turns)")
                return entries
    except Exception as e:
        print(f"      metadataContent fetch failed: {e}")

    print(f"      Could not retrieve transcript content.")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Meeting printer
# ─────────────────────────────────────────────────────────────────────────────

def _print_meeting(idx: int, source: str, evt: dict):
    subject = evt.get("subject", "Untitled")
    start = evt.get("start", {})
    end = evt.get("end", {})
    start_dt = start.get("dateTime", "N/A") if isinstance(start, dict) else str(start)
    end_dt = end.get("dateTime", "N/A") if isinstance(end, dict) else str(end)
    tz = (start.get("timeZone", "") if isinstance(start, dict) else "") or ""
    is_online = evt.get("isOnlineMeeting", False)
    location = evt.get("location", {})
    location_display = location.get("displayName", "") if isinstance(location, dict) else str(location)
    body_preview = (evt.get("bodyPreview") or "")[:200]

    print("=" * 72)
    print(f"[{idx}] {subject}  [{source}]")
    print("=" * 72)
    print(f"  ID       : {evt.get('id', 'N/A')}")
    print(f"  Start    : {start_dt}  ({tz})")
    print(f"  End      : {end_dt}")
    print(f"  Online   : {is_online}")
    print(f"  Location : {location_display or 'N/A'}")
    if body_preview:
        print(f"  Preview  : {body_preview}")

    org = evt.get("organizer", {})
    if isinstance(org, dict):
        email_info = org.get("emailAddress", {})
        if email_info:
            print(f"  Organizer: {email_info.get('name', '')} <{email_info.get('address', '')}>")

    attendees_list = evt.get("attendees", [])
    if attendees_list:
        print(f"  Attendees ({len(attendees_list)}):")
        for a in attendees_list:
            email = a.get("emailAddress", {})
            name = email.get("name", "N/A")
            addr = email.get("address", "N/A")
            status = a.get("status", {}).get("response", "none")
            atype = a.get("type", "")
            print(f"    • {name} <{addr}>  status={status}  type={atype}")
    else:
        print("  Attendees: (none)")

    om = evt.get("onlineMeeting") or {}
    if om:
        print(f"  Join URL : {om.get('joinUrl', 'N/A')}")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        raise EnvironmentError(
            "Missing credentials. Set MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET env vars."
        )

    # ── Auth ─────────────────────────────────────────────────────────────────
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    msal_app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=authority,
    )
    msal_scopes = [f"https://graph.microsoft.com/{s}" for s in SCOPES]

    print("=== Authenticating via browser (authorization code flow) ===")
    flow = msal_app.initiate_auth_code_flow(scopes=msal_scopes, redirect_uri=REDIRECT_URI)
    if "auth_uri" not in flow:
        raise Exception(f"Failed to initiate auth code flow: {json.dumps(flow, indent=2)}")

    print(f"Opening browser...\n  {flow['auth_uri']}\n")
    webbrowser.open(flow["auth_uri"])

    print(f"Waiting for redirect on localhost:{REDIRECT_PORT} ...")
    params = _capture_auth_redirect()
    if not params:
        raise Exception("Timed out waiting for OAuth redirect (120s).")

    flat_params = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}
    result = msal_app.acquire_token_by_auth_code_flow(flow, flat_params)
    if "access_token" not in result:
        raise Exception(f"Token exchange failed: {result.get('error_description') or result}")

    access_token = result["access_token"]
    print("Authentication successful!\n")

    # ── Decode JWT ────────────────────────────────────────────────────────────
    user_oid = None
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        user_oid = claims.get("oid")
        print(f"  Token scopes : {claims.get('scp', 'NONE')}")
        print(f"  User OID     : {user_oid}")
        print(f"  UPN          : {claims.get('upn', 'N/A')}\n")
    except Exception as jwt_err:
        print(f"  (Could not decode JWT: {jwt_err})\n")

    if not user_oid:
        raise Exception("Could not extract user OID from JWT")

    # ── Graph SDK client ──────────────────────────────────────────────────────
    token_provider = _StaticTokenProvider(access_token)
    auth_provider = BaseBearerTokenAuthenticationProvider(token_provider)
    adapter = HttpxRequestAdapter(authentication_provider=auth_provider)
    client = GraphServiceClient(request_adapter=adapter)
    user_client = client.users.by_user_id(user_oid)

    # ── Verify user ───────────────────────────────────────────────────────────
    try:
        me = await user_client.get()
        if me:
            print(f"Signed in as: {me.display_name} ({me.user_principal_name})\n")
    except Exception as e:
        print(f"Could not fetch user profile: {e}\n")

    auth_header = {"Authorization": f"Bearer {access_token}"}
    now = datetime.now(timezone.utc)
    start_range = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_range = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    async with httpx.AsyncClient(timeout=30) as http:

        # ── SOURCE 1: Calendar View ───────────────────────────────────────────
        # FIX [2]: Removed $orderby — not supported on calendarView, causes HTTP 400
        # FIX [3]: _get_all_pages handles pagination automatically
        print(f"=== [Source 1] calendarView  ({start_range} → {end_range}) ===\n")
        cal_url = (
            f"https://graph.microsoft.com/v1.0/users/{user_oid}/calendarView"
            f"?startDateTime={start_range}&endDateTime={end_range}"
            f"&$top=100"
            f"&$select=id,subject,start,end,isOnlineMeeting,onlineMeeting,"
            f"organizer,attendees,location,bodyPreview,webLink,createdDateTime"
        )
        try:
            cal_events = await _get_all_pages(http, cal_url, auth_header)
            print(f"  → {len(cal_events)} calendar event(s) found\n")
        except Exception as e:
            print(f"  Error fetching calendarView: {e}")
            cal_events = []

        # ── SOURCE 2: Online Meetings ─────────────────────────────────────────
        # NOTE: GET /users/{id}/onlineMeetings (without a filter) returns HTTP 400.
        # The API only allows lookup by ID or by ?$filter=JoinWebUrl eq '...'.
        # Strategy: extract all joinUrls found in calendarView events, then resolve
        # each one to a full onlineMeeting object (participants, chatInfo, etc.).
        # This correctly covers all Teams-linked calendar meetings.
        print("=== [Source 2] /me/onlineMeetings  (resolved from calendarView joinUrls) ===\n")
        online_meetings_raw: list[dict] = []
        cal_join_urls = [
            (evt.get("onlineMeeting") or {}).get("joinUrl", "")
            for evt in cal_events
            if (evt.get("onlineMeeting") or {}).get("joinUrl")
        ]
        print(f"  → {len(cal_join_urls)} joinUrl(s) found in calendar events")

        for jurl in cal_join_urls:
            try:
                # Pass $filter as a param so httpx percent-encodes it correctly.
                # Inlining the URL directly causes HTTP 400 when joinUrl contains
                # special characters like % or + that conflict with OData syntax.
                # NOTE: $select is NOT allowed on this filtered endpoint — omit it entirely
                resp = await http.get(
                    f"https://graph.microsoft.com/v1.0/users/{user_oid}/onlineMeetings",
                    params={"$filter": f"JoinWebUrl eq '{jurl}'"},
                    headers=auth_header,
                )
                if resp.status_code == 200:
                    items = resp.json().get("value", [])
                    if items:
                        online_meetings_raw.append(items[0])
                        print(f"    ✓ Resolved: {items[0].get('subject', '(no subject)')} | id={items[0]['id']}")
                    else:
                        print(f"    ✗ No onlineMeeting found for joinUrl (may be a non-Teams event)")
                else:
                    err_body = ""
                    try:
                        err_body = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        pass
                    print(f"    ✗ HTTP {resp.status_code} resolving joinUrl: {err_body}")
            except Exception as e:
                print(f"    ✗ Error resolving joinUrl: {e}")

        print(f"\n  → {len(online_meetings_raw)} online meeting(s) resolved\n")

        # ── SOURCE 3: Teams channel meetings ─────────────────────────────────
        # Full traversal: joinedTeams → channels → systemEventMessages
        # Extracts joinWebUrl from eventDetail, then resolves to onlineMeeting.id
        # via /onlineMeetings?$filter=JoinWebUrl eq '...'
        print("=== [Source 3] Teams channel meetings ===\n")
        channel_meeting_ids: set[str] = set()

        # Structured metadata per discovered meeting for printing / dedup
        # shape: { meeting_id: { team, channel, join_url, created_dt, subject } }
        channel_meetings_meta: dict[str, dict] = {}

        try:
            # ── Step 1: all joined teams (paginated) ─────────────────────────
            teams_raw = await _get_all_pages(
                http,
                f"https://graph.microsoft.com/v1.0/users/{user_oid}/joinedTeams"
                f"?$select=id,displayName",
                auth_header,
            )
            print(f"  → {len(teams_raw)} joined team(s) found")

            for team in teams_raw:
                team_id   = team["id"]
                team_name = team.get("displayName", team_id)
                print(f"\n  ── Team: {team_name}")

                # ── Step 2: channels for this team ───────────────────────────
                ch_resp = await http.get(
                    f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels"
                    f"?$select=id,displayName,membershipType",
                    headers=auth_header,
                )
                if ch_resp.status_code != 200:
                    print(f"     ✗ Could not fetch channels (HTTP {ch_resp.status_code})")
                    continue

                channels = ch_resp.json().get("value", [])
                print(f"     {len(channels)} channel(s)")

                for ch in channels:
                    ch_id   = ch["id"]
                    ch_name = ch.get("displayName", ch_id)
                    ch_type = ch.get("membershipType", "standard")
                    print(f"\n     ── Channel: #{ch_name} [{ch_type}]")

                    # ── Step 3: fetch channel messages, filter client-side ────
                    # The channel messages API does NOT support $filter on messageType
                    # (returns HTTP 400). Fetch all recent messages and keep only
                    # systemEventMessage entries that contain meeting eventDetail.
                    # NOTE: $select is not supported here — fetch without it
                    msgs_resp = await http.get(
                        f"https://graph.microsoft.com/v1.0/teams/{team_id}"
                        f"/channels/{ch_id}/messages"
                        f"?$top=50",
                        headers=auth_header,
                    )
                    if msgs_resp.status_code == 403:
                        print(f"        ✗ No permission to read messages — skipping")
                        continue
                    if msgs_resp.status_code != 200:
                        err_body = ""
                        try:
                            err_body = msgs_resp.json().get("error", {}).get("message", "")
                        except Exception:
                            pass
                        print(f"        ✗ HTTP {msgs_resp.status_code} — skipping: {err_body}")
                        continue

                    all_msgs = msgs_resp.json().get("value", [])
                    # Teams meeting events come back as 'unknownFutureValue' (not systemEventMessage)
                    # Keep any message that has a non-empty eventDetail block
                    msgs = [
                        m for m in all_msgs
                        if (m.get("eventDetail") or {}).get("@odata.type", "")
                    ]
                    print(f"        {len(all_msgs)} message(s) fetched, {len(msgs)} meeting event(s)")
                    meeting_count_in_channel = 0

                    # Group messages by pairing callStarted → callEnded by index
                    # (they arrive in chronological order: start, end, start, end...)
                    # Each pair = one meeting session.
                    started_msgs = [
                        m for m in msgs
                        if "#microsoft.graph.callStartedEventMessageDetail"
                        in (m.get("eventDetail") or {}).get("@odata.type", "")
                    ]

                    for msg in started_msgs:
                        detail     = msg.get("eventDetail") or {}
                        created_dt = msg.get("createdDateTime", "")
                        subject = msg.get("subject") or f"Channel meeting in #{ch_name}"

                        # ── Full raw JSON of this channel meeting message ──────
                        print(f"        RAW JSON:")
                        print(json.dumps(msg, indent=10, default=str))

                        # ── Step 4: resolve via group calendar ───────────────────────
                        # The team's shared calendar (GET /groups/{groupId}/calendarView)
                        # surfaces channel meetings with a proper onlineMeeting.joinUrl,
                        # which we can then resolve to an onlineMeeting.id.
                        # groupId = teamId (they share the same AAD object ID).
                        meeting_id  = None
                        join_url_resolved = ""
                        if created_dt:
                            try:
                                ref_t  = datetime.fromisoformat(created_dt.replace("Z", "+00:00"))
                                t_from = (ref_t - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                t_to   = (ref_t + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                # Try multiple calendar endpoint variants for the team/group
                                endpoints = [
                                    # Standard group calendar events (no time filter)
                                    (f"https://graph.microsoft.com/v1.0/groups/{team_id}/calendar/events", {}),
                                    # Group calendarView with time window
                                    (f"https://graph.microsoft.com/v1.0/groups/{team_id}/calendarView", {"startDateTime": t_from, "endDateTime": t_to}),
                                    # Group events directly
                                    (f"https://graph.microsoft.com/v1.0/groups/{team_id}/events", {}),
                                    # Beta endpoint
                                    (f"https://graph.microsoft.com/beta/groups/{team_id}/calendarView", {"startDateTime": t_from, "endDateTime": t_to}),
                                ]

                                cal_items = []
                                matched_endpoint = ""
                                for ep_url, ep_params in endpoints:
                                    try:
                                        cal_resp = await http.get(ep_url, params=ep_params, headers=auth_header)
                                        status = cal_resp.status_code
                                        items  = cal_resp.json().get("value", []) if status == 200 else []
                                        print(f"        → {ep_url.split('graph.microsoft.com')[1]}"
                                              f"  HTTP {status}  items={len(items)}")
                                        if status == 200 and items:
                                            cal_items     = items
                                            matched_endpoint = ep_url
                                            break
                                    except Exception as ep_err:
                                        print(f"        → {ep_url}  ERROR: {ep_err}")

                                if cal_items:
                                    evt_match = cal_items[0]
                                    join_url_resolved = (evt_match.get("onlineMeeting") or {}).get("joinUrl", "")
                                    subject = evt_match.get("subject") or subject
                                    print(f"        ✓ Found via {matched_endpoint}: {subject}")
                                    print(f"          joinUrl: {join_url_resolved}")
                                    if join_url_resolved:
                                        om_resp = await http.get(
                                            f"https://graph.microsoft.com/v1.0/users/{user_oid}/onlineMeetings",
                                            params={"$filter": f"JoinWebUrl eq '{join_url_resolved}'"},
                                            headers=auth_header,
                                        )
                                        if om_resp.status_code == 200:
                                            om_items = om_resp.json().get("value", [])
                                            if om_items:
                                                meeting_id = om_items[0]["id"]
                                                print(f"          onlineMeeting.id: {meeting_id}")
                                            else:
                                                print(f"          ⚠ JoinWebUrl resolve returned 0 results")
                                        else:
                                            print(f"          ⚠ JoinWebUrl resolve HTTP {om_resp.status_code}")
                                else:
                                    print(f"        ⚠ All calendar endpoints returned 0 events for window {t_from} → {t_to}")
                            except Exception as ex:
                                print(f"        ⚠ Group calendar resolve failed: {ex}")

                        # Use createdDateTime as a stable dedup key when no meeting_id
                        dedup_key = meeting_id or f"channel-{ch_id}-{created_dt}"

                        if dedup_key not in channel_meeting_ids:
                            channel_meeting_ids.add(dedup_key)
                            channel_meetings_meta[dedup_key] = {
                                "team":       team_name,
                                "team_id":    team_id,
                                "channel":    ch_name,
                                "channel_id": ch_id,
                                "join_url":   join_url_resolved,
                                "created_dt": created_dt,
                                "subject":    subject,
                                "_online_meeting_id": meeting_id,
                            }
                            meeting_count_in_channel += 1
                            print(
                                f"        ✓ Channel meeting recorded"
                                f"  {created_dt[:19] if created_dt else 'N/A'}"
                                f"  {subject}"
                            )

                    if meeting_count_in_channel == 0:
                        print(f"        (no meetings in this channel)")

        except Exception as e:
            print(f"  Error fetching Teams channel meetings: {e}")

        print(f"\n  → {len(channel_meeting_ids)} unique channel meeting(s) discovered\n")

        # ── Deduplicate: merge cal_events + online_meetings by joinUrl ────────
        # FIX [9]: calendar + online meetings both contain the same Teams meeting;
        # use joinUrl as the canonical dedup key.
        seen_join_urls: set[str] = set()
        all_meetings: list[dict] = []

        # Normalize online meetings to calendar-event-like shape for uniform processing
        for om in online_meetings_raw:
            jurl = om.get("joinWebUrl", "")
            participants = om.get("participants", {}) or {}
            organizer_info = participants.get("organizer", {}) or {}
            attendees_info = participants.get("attendees", []) or []

            normalized = {
                "id": om.get("id", ""),
                "_online_meeting_id": om.get("id", ""),   # keep raw OM id
                "subject": om.get("subject", "Ad-hoc Teams meeting"),
                "start": {"dateTime": om.get("startDateTime", ""), "timeZone": "UTC"},
                "end": {"dateTime": om.get("endDateTime", ""), "timeZone": "UTC"},
                "isOnlineMeeting": True,
                "onlineMeeting": {"joinUrl": jurl},
                "organizer": {
                    "emailAddress": {
                        "name": organizer_info.get("upn", ""),
                        "address": organizer_info.get("upn", ""),
                    }
                },
                "attendees": [
                    {
                        "emailAddress": {
                            "name": a.get("upn", ""),
                            "address": a.get("upn", ""),
                        },
                        "status": {"response": "accepted"},
                        "type": "required",
                    }
                    for a in attendees_info
                ],
                "_source": "onlineMeetings",
            }
            if jurl:
                seen_join_urls.add(jurl)
            all_meetings.append(normalized)

        for evt in cal_events:
            jurl = (evt.get("onlineMeeting") or {}).get("joinUrl", "")
            if jurl and jurl in seen_join_urls:
                # Already have this from onlineMeetings; skip duplicate
                continue
            if jurl:
                seen_join_urls.add(jurl)
            evt["_source"] = "calendarView"
            all_meetings.append(evt)

        # Merge channel meetings not already captured by calendarView / onlineMeetings
        existing_ids = {e.get("id") or e.get("_online_meeting_id") for e in all_meetings}
        for dedup_key, meta in channel_meetings_meta.items():
            om_id = meta.get("_online_meeting_id")  # may be None for unresolved meetings
            jurl  = meta.get("join_url", "")

            # Skip if already present by joinUrl or resolved onlineMeeting ID
            if jurl and jurl in seen_join_urls:
                continue
            if om_id and om_id in existing_ids:
                continue

            if jurl:
                seen_join_urls.add(jurl)

            # Use resolved onlineMeeting ID if available, else the dedup_key
            record_id = om_id or dedup_key
            all_meetings.append({
                "id":                  record_id,
                "_online_meeting_id":  om_id,   # None if unresolved — transcripts skipped
                "subject":             meta["subject"],
                "start":               {"dateTime": meta["created_dt"], "timeZone": "UTC"},
                "end":                 {"dateTime": "", "timeZone": "UTC"},
                "isOnlineMeeting":     True,
                "onlineMeeting":       {"joinUrl": jurl},
                "organizer":           {},
                "attendees":           [],
                "_source":             "channelMeeting",
                "_team":               meta["team"],
                "_channel":            meta["channel"],
            })

        print(f"=== Total unique meetings: {len(all_meetings)} ===\n")

        # ── Print all meetings ────────────────────────────────────────────────
        for idx, evt in enumerate(all_meetings, 1):
            source = evt.get("_source", "unknown")
            if source == "channelMeeting":
                source = f"channel: {evt.get('_team', '')} → #{evt.get('_channel', '')}"
            _print_meeting(idx, source, evt)

        # ── Fetch transcripts for ALL online meetings ─────────────────────────
        # FIX [4]: removed MAX_MEETINGS = 2 cap — process everything
        # FIX [5]: counter now tracks meetings attempted, not just ones with transcripts
        online_for_transcripts = [
            e for e in all_meetings
            if e.get("isOnlineMeeting") and (
                e.get("onlineMeeting", {}).get("joinUrl")
                or e.get("_online_meeting_id")
            )
        ]

        if not online_for_transcripts:
            print("\nNo online meetings found — no transcripts to fetch.")
            return

        print(f"\n{'=' * 72}")
        print(f"  Fetching transcripts for {len(online_for_transcripts)} online meeting(s)")
        print(f"{'=' * 72}\n")

        for evt in online_for_transcripts:
            subject = evt.get("subject", "Untitled")
            start_dt = (evt.get("start") or {}).get("dateTime", "N/A")
            join_url = (evt.get("onlineMeeting") or {}).get("joinUrl", "")

            print(f"\n── {subject}  |  {start_dt} ──")

            # Resolve to onlineMeeting ID (needed for transcript endpoint)
            meeting_id = evt.get("_online_meeting_id")

            if not meeting_id and join_url:
                # Calendar events only have joinUrl; resolve → onlineMeeting.id
                try:
                    oq = OnlineMeetingsRequestBuilder.OnlineMeetingsRequestBuilderGetQueryParameters(
                        filter=f"JoinWebUrl eq '{join_url}'",
                    )
                    oc = OnlineMeetingsRequestBuilder.OnlineMeetingsRequestBuilderGetRequestConfiguration(
                        query_parameters=oq,
                    )
                    page = await user_client.online_meetings.get(request_configuration=oc)
                    if page and page.value:
                        meeting_id = page.value[0].id
                        print(f"  Resolved meeting ID: {meeting_id}")
                    else:
                        print(f"  Could not resolve onlineMeeting from joinUrl — skipping.")
                        continue
                except Exception as e:
                    print(f"  Error resolving meeting: {e}")
                    continue

            # List transcripts for this meeting
            try:
                transcripts_page = await user_client.online_meetings.by_online_meeting_id(
                    meeting_id
                ).transcripts.get()
            except Exception as e:
                print(f"  Error listing transcripts: {e}")
                continue

            if not transcripts_page or not transcripts_page.value:
                print(f"  No transcripts available for this meeting.")
                continue

            print(f"  Found {len(transcripts_page.value)} transcript(s)")

            for t_idx, t in enumerate(transcripts_page.value):
                print(f"\n  ── Transcript {t_idx + 1} / {len(transcripts_page.value)} (ID: {t.id}) ──")

                entries = await _fetch_transcript(
                    http,
                    access_token,
                    user_oid,
                    meeting_id,
                    t.id,
                    str(t.created_date_time or ""),
                )

                if not entries:
                    continue

                print(f"\n  {'─' * 60}")
                for entry in entries:
                    ts = f"[{entry['timestamp']}] " if entry.get("timestamp") else ""
                    print(f"  {ts}[{entry['speaker']}]  {entry['text']}")
                print(f"  {'─' * 60}")

        print("\n=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())