"""Gong External API Service

This module provides high-level operations for interacting with Gong data,
building on top of the base Gong client.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.sources.client.gong.gong import GongClient


class GongService:
    """High-level service for Gong API operations.

    This service provides business logic and data transformation
    on top of the base GongClient.
    """

    def __init__(self, client: GongClient):
        """Initialize Gong service.

        Args:
            client: Configured GongClient instance

        """
        self.client = client

    async def validate_connection(self) -> tuple[bool, str | None]:
        """Validate connection to Gong API.

        Returns:
            Tuple of (is_valid, error_message)

        """
        try:
            is_connected = await self.client.test_connection()
            if is_connected:
                return True, None
            return False, "Unable to connect to Gong API. Please check your credentials."
        except Exception as e:
            return False, f"Connection error: {e!s}"

    async def get_workspace_info(self) -> dict[str, Any]:
        """Get workspace information and statistics.

        Returns:
            Dictionary containing workspace info and stats

        """
        try:
            workspaces = await self.client.get_workspaces()
            users = await self.client.get_users(limit=1)  # Just to get total count

            workspace_list = workspaces.get("workspaces", [])
            user_count = users.get("records", {}).get("totalRecords", 0)

            return {
                "workspaces": workspace_list,
                "total_workspaces": len(workspace_list),
                "total_users": user_count,
                "primary_workspace": workspace_list[0] if workspace_list else None,
            }
        except Exception as e:
            return {
                "error": f"Failed to get workspace info: {e!s}",
                "workspaces": [],
                "total_workspaces": 0,
                "total_users": 0,
                "primary_workspace": None,
            }

    async def get_recent_calls(
        self,
        days_back: int = 30,
        workspace_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent calls from the specified number of days back.

        Args:
            days_back: Number of days to look back
            workspace_id: Specific workspace ID (optional)
            limit: Maximum number of calls to return (optional)

        Returns:
            List of call records

        """
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days_back)

        from_date = start_date.isoformat().replace("+00:00", "Z")
        to_date = end_date.isoformat().replace("+00:00", "Z")

        try:
            if limit and limit <= 100:
                # Single request if limit is small
                response = await self.client.get_calls(
                    from_date=from_date,
                    to_date=to_date,
                    limit=limit,
                    workspace_id=workspace_id,
                )
                return response.get("calls", [])
            # Get all calls and optionally limit
            calls = await self.client.get_all_calls(
                from_date=from_date,
                to_date=to_date,
                workspace_id=workspace_id,
            )
            return calls[:limit] if limit else calls

        except Exception as e:
            print(f"Error getting recent calls: {e}")
            return []

    async def get_call_with_transcript(self, call_id: str) -> dict[str, Any]:
        """Get call details along with transcript.

        Args:
            call_id: Unique call identifier

        Returns:
            Combined call details and transcript data

        """
        try:
            # Get call details and transcript concurrently
            call_details_task = self.client.get_call_details(call_id)
            transcript_task = self.client.get_call_transcript(call_id)

            call_details, transcript = await asyncio.gather(
                call_details_task,
                transcript_task,
                return_exceptions=True,
            )

            result = {"call_id": call_id}

            # Handle call details
            if isinstance(call_details, Exception):
                result["call_details_error"] = str(call_details)
                result["call_details"] = None
            else:
                result["call_details"] = call_details

            # Handle transcript
            if isinstance(transcript, Exception):
                result["transcript_error"] = str(transcript)
                result["transcript"] = None
            else:
                result["transcript"] = transcript

            return result

        except Exception as e:
            return {
                "call_id": call_id,
                "error": f"Failed to get call data: {e!s}",
                "call_details": None,
                "transcript": None,
            }

    async def get_user_activity_summary(
        self,
        user_id: str | None = None,
        days_back: int = 30,
    ) -> dict[str, Any]:
        """Get activity summary for a user or all users.

        Args:
            user_id: Specific user ID (optional, gets all users if None)
            days_back: Number of days to analyze

        Returns:
            User activity summary

        """
        try:
            # Get recent calls
            calls = await self.get_recent_calls(days_back=days_back)

            if user_id:
                # Filter calls for specific user
                user_calls = [
                    call for call in calls
                    if any(
                        participant.get("userId") == user_id
                        for participant in call.get("participants", [])
                    )
                ]
            else:
                user_calls = calls

            # Calculate statistics
            total_calls = len(user_calls)
            total_duration = sum(
                call.get("duration", 0) for call in user_calls
            )

            # Group by date
            calls_by_date = {}
            for call in user_calls:
                call_date = call.get("started", "")[:10]  # Extract date part
                if call_date:
                    calls_by_date[call_date] = calls_by_date.get(call_date, 0) + 1

            return {
                "user_id": user_id,
                "period_days": days_back,
                "total_calls": total_calls,
                "total_duration_seconds": total_duration,
                "average_call_duration": total_duration / total_calls if total_calls > 0 else 0,
                "calls_by_date": calls_by_date,
                "most_active_date": max(calls_by_date.items(), key=lambda x: x[1]) if calls_by_date else None,
            }

        except Exception as e:
            return {
                "user_id": user_id,
                "error": f"Failed to get activity summary: {e!s}",
                "total_calls": 0,
                "total_duration_seconds": 0,
            }

    async def search_calls_by_keywords(
        self,
        keywords: list[str],
        days_back: int = 30,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for calls containing specific keywords in titles or participants.

        Note: This is a basic search. Gong API doesn't provide full-text search,
        so this searches in call titles and participant names.

        Args:
            keywords: List of keywords to search for
            days_back: Number of days to search back
            workspace_id: Specific workspace ID (optional)

        Returns:
            List of matching calls

        """
        try:
            calls = await self.get_recent_calls(
                days_back=days_back,
                workspace_id=workspace_id,
            )

            matching_calls = []
            keywords_lower = [kw.lower() for kw in keywords]

            for call in calls:
                # Search in call title
                title = call.get("title", "").lower()

                # Search in participant names
                participant_names = []
                for participant in call.get("participants", []):
                    name = participant.get("name", "").lower()
                    if name:
                        participant_names.append(name)

                # Check if any keyword matches
                text_to_search = f"{title} {' '.join(participant_names)}"

                if any(keyword in text_to_search for keyword in keywords_lower):
                    # Add match score
                    match_count = sum(
                        1 for keyword in keywords_lower
                        if keyword in text_to_search
                    )
                    call["match_score"] = match_count
                    call["matched_keywords"] = [
                        kw for kw in keywords
                        if kw.lower() in text_to_search
                    ]
                    matching_calls.append(call)

            # Sort by match score (highest first)
            matching_calls.sort(key=lambda x: x.get("match_score", 0), reverse=True)

            return matching_calls

        except Exception as e:
            print(f"Error searching calls: {e}")
            return []

    async def get_team_performance_metrics(
        self,
        workspace_id: str | None = None,
        days_back: int = 30,
    ) -> dict[str, Any]:
        """Get team performance metrics.

        Args:
            workspace_id: Specific workspace ID (optional)
            days_back: Number of days to analyze

        Returns:
            Team performance metrics

        """
        try:
            # Get users and calls concurrently
            users_task = self.client.get_all_users()
            calls_task = self.get_recent_calls(days_back=days_back, workspace_id=workspace_id)

            users, calls = await asyncio.gather(users_task, calls_task)

            # Calculate metrics per user
            user_metrics = {}
            for user in users:
                user_id = user.get("id")
                user_name = user.get("firstName", "") + " " + user.get("lastName", "")

                user_calls = [
                    call for call in calls
                    if any(
                        participant.get("userId") == user_id
                        for participant in call.get("participants", [])
                    )
                ]

                total_duration = sum(call.get("duration", 0) for call in user_calls)

                user_metrics[user_id] = {
                    "name": user_name.strip(),
                    "email": user.get("emailAddress", ""),
                    "total_calls": len(user_calls),
                    "total_duration_seconds": total_duration,
                    "average_call_duration": total_duration / len(user_calls) if user_calls else 0,
                }

            # Calculate team totals
            total_team_calls = len(calls)
            total_team_duration = sum(call.get("duration", 0) for call in calls)
            active_users = len([m for m in user_metrics.values() if m["total_calls"] > 0])

            return {
                "workspace_id": workspace_id,
                "period_days": days_back,
                "team_totals": {
                    "total_calls": total_team_calls,
                    "total_duration_seconds": total_team_duration,
                    "active_users": active_users,
                    "total_users": len(users),
                },
                "user_metrics": user_metrics,
                "top_performers": sorted(
                    user_metrics.items(),
                    key=lambda x: x[1]["total_calls"],
                    reverse=True,
                )[:5],  # Top 5 by call count
            }

        except Exception as e:
            return {
                "workspace_id": workspace_id,
                "error": f"Failed to get team metrics: {e!s}",
                "team_totals": {},
                "user_metrics": {},
                "top_performers": [],
            }

    async def export_calls_to_dict(
        self,
        days_back: int = 7,
        include_transcripts: bool = False,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Export calls data to a list of dictionaries for further processing.

        Args:
            days_back: Number of days to export
            include_transcripts: Whether to include transcript data
            workspace_id: Specific workspace ID (optional)

        Returns:
            List of call dictionaries with optional transcript data

        """
        try:
            calls = await self.get_recent_calls(
                days_back=days_back,
                workspace_id=workspace_id,
            )

            if not include_transcripts:
                return calls

            # Get transcripts for all calls (with concurrency limit)
            semaphore = asyncio.Semaphore(5)  # Limit concurrent requests

            async def get_call_with_transcript_limited(call):
                async with semaphore:
                    call_id = call.get("id")
                    if call_id:
                        transcript_data = await self.get_call_with_transcript(call_id)
                        call["transcript_data"] = transcript_data.get("transcript")
                    return call

            # Process calls with transcripts
            calls_with_transcripts = await asyncio.gather(
                *[get_call_with_transcript_limited(call) for call in calls],
                return_exceptions=True,
            )

            # Filter out exceptions
            valid_calls = [
                call for call in calls_with_transcripts
                if not isinstance(call, Exception)
            ]

            return valid_calls

        except Exception as e:
            print(f"Error exporting calls: {e}")
            return []


# Factory function
async def create_gong_service(access_key: str, access_key_secret: str) -> GongService:
    """Create a configured Gong service.

    Args:
        access_key: Gong API access key
        access_key_secret: Gong API access key secret

    Returns:
        Configured GongService instance

    """
    client = GongClient(access_key, access_key_secret)
    return GongService(client)
