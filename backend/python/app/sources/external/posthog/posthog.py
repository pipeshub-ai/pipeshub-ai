from typing import Any, Dict, List, Optional

from app.sources.client.posthog.posthog import PostHogClient, PostHogResponse


class PostHogDataSource:
    """
    Complete PostHog GraphQL API client wrapper.
    Auto-generated wrapper for PostHog analytics and product analytics operations.

    Coverage:
    - Events: Query and capture events
    - Persons: Query and update person data
    - Actions: Define and manage custom actions
    - Cohorts: Create and manage user cohorts
    - Dashboards: Create and manage dashboards
    - Insights: Create trends, funnels, and other analytics
    - Feature Flags: Manage feature flags
    - Experiments: A/B testing and experiments
    - Session Recordings: Access session recordings
    """

    def __init__(self, posthog_client: PostHogClient) -> None:
        """Initialize PostHog data source.
        Args:
            posthog_client: PostHogClient instance
        """
        self._posthog_client = posthog_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

    async def events(
        self,
        after: Optional[str] = None,
        before: Optional[str] = None,
        distinct_id: Optional[str] = None,
        event: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None
    ) -> PostHogResponse:
        """Query events with filtering and pagination

        GraphQL Operation: Query

        Args:
            after (str, optional): Parameter for after
            before (str, optional): Parameter for before
            distinct_id (str, optional): Parameter for distinct_id
            event (str, optional): Parameter for event
            properties (Dict[str, Any], optional): Parameter for properties
            limit (int, optional): Parameter for limit

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if after is not None:
            variables["after"] = after
        if before is not None:
            variables["before"] = before
        if distinct_id is not None:
            variables["distinct_id"] = distinct_id
        if event is not None:
            variables["event"] = event
        if properties is not None:
            variables["properties"] = properties
        if limit is not None:
            variables["limit"] = limit

        query = '''query Events($after: String, $before: String, $distinct_id: String, $event: String, $properties: JSON, $limit: Int) {
                            events(after: $after, before: $before, distinct_id: $distinct_id, event: $event, properties: $properties, limit: $limit) {
                                results {
                                    id
                                    event
                                    timestamp
                                    distinct_id
                                    properties
                                    person {
                                        id
                                        name
                                        properties
                                    }
                                }
                                next
                                previous
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="events"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query events: {str(e)}"
            )

    async def event(
        self,
        id: str
    ) -> PostHogResponse:
        """Get single event by ID

        GraphQL Operation: Query

        Args:
            id (str, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Event($id: ID!) {
                            event(id: $id) {
                                id
                                event
                                timestamp
                                distinct_id
                                properties
                                person {
                                    id
                                    name
                                    properties
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="event"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query event: {str(e)}"
            )

    async def persons(
        self,
        search: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        cohort: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Query persons with filtering

        GraphQL Operation: Query

        Args:
            search (str, optional): Parameter for search
            properties (Dict[str, Any], optional): Parameter for properties
            cohort (int, optional): Parameter for cohort
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if search is not None:
            variables["search"] = search
        if properties is not None:
            variables["properties"] = properties
        if cohort is not None:
            variables["cohort"] = cohort
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Persons($search: String, $properties: JSON, $cohort: Int, $limit: Int, $offset: Int) {
                            persons(search: $search, properties: $properties, cohort: $cohort, limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    distinct_ids
                                    properties
                                    created_at
                                    updated_at
                                }
                                next
                                previous
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="persons"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query persons: {str(e)}"
            )

    async def person(
        self,
        id: str
    ) -> PostHogResponse:
        """Get person by ID

        GraphQL Operation: Query

        Args:
            id (str, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Person($id: ID!) {
                            person(id: $id) {
                                id
                                name
                                distinct_ids
                                properties
                                created_at
                                updated_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="person"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query person: {str(e)}"
            )

    async def actions(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all actions

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Actions($limit: Int, $offset: Int) {
                            actions(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    description
                                    steps {
                                        event
                                        url
                                        selector
                                        properties
                                    }
                                    created_at
                                    updated_at
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="actions"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query actions: {str(e)}"
            )

    async def action(
        self,
        id: int
    ) -> PostHogResponse:
        """Get action by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Action($id: Int!) {
                            action(id: $id) {
                                id
                                name
                                description
                                steps {
                                    event
                                    url
                                    selector
                                    properties
                                }
                                created_at
                                updated_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="action"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query action: {str(e)}"
            )

    async def cohorts(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all cohorts

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Cohorts($limit: Int, $offset: Int) {
                            cohorts(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    description
                                    is_static
                                    filters
                                    count
                                    created_at
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="cohorts"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query cohorts: {str(e)}"
            )

    async def cohort(
        self,
        id: int
    ) -> PostHogResponse:
        """Get cohort by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Cohort($id: Int!) {
                            cohort(id: $id) {
                                id
                                name
                                description
                                is_static
                                filters
                                count
                                created_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="cohort"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query cohort: {str(e)}"
            )

    async def dashboards(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all dashboards

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Dashboards($limit: Int, $offset: Int) {
                            dashboards(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    description
                                    pinned
                                    items {
                                        id
                                        name
                                        filters
                                    }
                                    created_at
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="dashboards"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query dashboards: {str(e)}"
            )

    async def dashboard(
        self,
        id: int
    ) -> PostHogResponse:
        """Get dashboard by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Dashboard($id: Int!) {
                            dashboard(id: $id) {
                                id
                                name
                                description
                                pinned
                                items {
                                    id
                                    name
                                    filters
                                }
                                created_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="dashboard"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query dashboard: {str(e)}"
            )

    async def insights(
        self,
        dashboard: Optional[int] = None,
        saved: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get insights with filtering

        GraphQL Operation: Query

        Args:
            dashboard (int, optional): Parameter for dashboard
            saved (bool, optional): Parameter for saved
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if dashboard is not None:
            variables["dashboard"] = dashboard
        if saved is not None:
            variables["saved"] = saved
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Insights($dashboard: Int, $saved: Boolean, $limit: Int, $offset: Int) {
                            insights(dashboard: $dashboard, saved: $saved, limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    filters
                                    result
                                    created_at
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="insights"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query insights: {str(e)}"
            )

    async def insight(
        self,
        id: int
    ) -> PostHogResponse:
        """Get insight by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Insight($id: Int!) {
                            insight(id: $id) {
                                id
                                name
                                filters
                                result
                                created_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="insight"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query insight: {str(e)}"
            )

    async def trend(
        self,
        events: List[Dict[str, Any]],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        interval: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> PostHogResponse:
        """Calculate trends for events

        GraphQL Operation: Query

        Args:
            events (List[Dict[str, Any]], required): Parameter for events
            date_from (str, optional): Parameter for date_from
            date_to (str, optional): Parameter for date_to
            interval (str, optional): Parameter for interval
            properties (Dict[str, Any], optional): Parameter for properties

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if events is not None:
            variables["events"] = events
        if date_from is not None:
            variables["date_from"] = date_from
        if date_to is not None:
            variables["date_to"] = date_to
        if interval is not None:
            variables["interval"] = interval
        if properties is not None:
            variables["properties"] = properties

        query = '''query Trend($events: [JSON!]!, $date_from: String, $date_to: String, $interval: String, $properties: JSON) {
                            trend(events: $events, date_from: $date_from, date_to: $date_to, interval: $interval, properties: $properties) {
                                result {
                                    labels
                                    data
                                    count
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="trend"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query trend: {str(e)}"
            )

    async def funnel(
        self,
        events: List[Dict[str, Any]],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        funnel_window_interval: Optional[int] = None,
        funnel_window_interval_unit: Optional[str] = None
    ) -> PostHogResponse:
        """Calculate funnel for event sequence

        GraphQL Operation: Query

        Args:
            events (List[Dict[str, Any]], required): Parameter for events
            date_from (str, optional): Parameter for date_from
            date_to (str, optional): Parameter for date_to
            funnel_window_interval (int, optional): Parameter for funnel_window_interval
            funnel_window_interval_unit (str, optional): Parameter for funnel_window_interval_unit

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if events is not None:
            variables["events"] = events
        if date_from is not None:
            variables["date_from"] = date_from
        if date_to is not None:
            variables["date_to"] = date_to
        if funnel_window_interval is not None:
            variables["funnel_window_interval"] = funnel_window_interval
        if funnel_window_interval_unit is not None:
            variables["funnel_window_interval_unit"] = funnel_window_interval_unit

        query = '''query Funnel($events: [JSON!]!, $date_from: String, $date_to: String, $funnel_window_interval: Int, $funnel_window_interval_unit: String) {
                            funnel(events: $events, date_from: $date_from, date_to: $date_to, funnel_window_interval: $funnel_window_interval, funnel_window_interval_unit: $funnel_window_interval_unit) {
                                result {
                                    steps {
                                        name
                                        count
                                        average_conversion_time
                                    }
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="funnel"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query funnel: {str(e)}"
            )

    async def feature_flags(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all feature flags

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query FeatureFlags($limit: Int, $offset: Int) {
                            featureFlags(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    key
                                    name
                                    filters
                                    active
                                    created_at
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="feature_flags"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query feature_flags: {str(e)}"
            )

    async def feature_flag(
        self,
        id: int
    ) -> PostHogResponse:
        """Get feature flag by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query FeatureFlag($id: Int!) {
                            featureFlag(id: $id) {
                                id
                                key
                                name
                                filters
                                active
                                created_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="feature_flag"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query feature_flag: {str(e)}"
            )

    async def experiments(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all experiments

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Experiments($limit: Int, $offset: Int) {
                            experiments(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    feature_flag
                                    parameters
                                    start_date
                                    end_date
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="experiments"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query experiments: {str(e)}"
            )

    async def experiment(
        self,
        id: int
    ) -> PostHogResponse:
        """Get experiment by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Experiment($id: Int!) {
                            experiment(id: $id) {
                                id
                                name
                                feature_flag
                                parameters
                                start_date
                                end_date
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="experiment"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query experiment: {str(e)}"
            )

    async def session_recordings(
        self,
        person_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get session recordings

        GraphQL Operation: Query

        Args:
            person_id (str, optional): Parameter for person_id
            date_from (str, optional): Parameter for date_from
            date_to (str, optional): Parameter for date_to
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if person_id is not None:
            variables["person_id"] = person_id
        if date_from is not None:
            variables["date_from"] = date_from
        if date_to is not None:
            variables["date_to"] = date_to
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query SessionRecordings($person_id: String, $date_from: String, $date_to: String, $limit: Int, $offset: Int) {
                            sessionRecordings(person_id: $person_id, date_from: $date_from, date_to: $date_to, limit: $limit, offset: $offset) {
                                results {
                                    id
                                    distinct_id
                                    viewed
                                    recording_duration
                                    start_time
                                    end_time
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="session_recordings"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query session_recordings: {str(e)}"
            )

    async def session_recording(
        self,
        id: str
    ) -> PostHogResponse:
        """Get session recording by ID

        GraphQL Operation: Query

        Args:
            id (str, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query SessionRecording($id: ID!) {
                            sessionRecording(id: $id) {
                                id
                                distinct_id
                                viewed
                                recording_duration
                                start_time
                                end_time
                                snapshot_data
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="session_recording"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query session_recording: {str(e)}"
            )

    async def organization(self) -> PostHogResponse:
        """Get current organization

        GraphQL Operation: Query

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}

        query = '''query Organization {
                            organization {
                                id
                                name
                                created_at
                                updated_at
                                membership_level
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="organization"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query organization: {str(e)}"
            )

    async def team(self) -> PostHogResponse:
        """Get current team

        GraphQL Operation: Query

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}

        query = '''query Team {
                            team {
                                id
                                name
                                created_at
                                updated_at
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="team"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query team: {str(e)}"
            )

    async def plugins(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> PostHogResponse:
        """Get all plugins

        GraphQL Operation: Query

        Args:
            limit (int, optional): Parameter for limit
            offset (int, optional): Parameter for offset

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if offset is not None:
            variables["offset"] = offset

        query = '''query Plugins($limit: Int, $offset: Int) {
                            plugins(limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    description
                                    url
                                    config_schema
                                    enabled
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="plugins"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query plugins: {str(e)}"
            )

    async def plugin(
        self,
        id: int
    ) -> PostHogResponse:
        """Get plugin by ID

        GraphQL Operation: Query

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''query Plugin($id: Int!) {
                            plugin(id: $id) {
                                id
                                name
                                description
                                url
                                config_schema
                                enabled
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="plugin"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute query plugin: {str(e)}"
            )

    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

    async def capture_event(
        self,
        event: str,
        distinct_id: str,
        properties: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None
    ) -> PostHogResponse:
        """Capture a new event

        GraphQL Operation: Mutation

        Args:
            event (str, required): Parameter for event
            distinct_id (str, required): Parameter for distinct_id
            properties (Dict[str, Any], optional): Parameter for properties
            timestamp (str, optional): Parameter for timestamp

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if event is not None:
            variables["event"] = event
        if distinct_id is not None:
            variables["distinct_id"] = distinct_id
        if properties is not None:
            variables["properties"] = properties
        if timestamp is not None:
            variables["timestamp"] = timestamp

        query = '''mutation CaptureEvent($event: String!, $distinct_id: String!, $properties: JSON, $timestamp: String) {
                            captureEvent(event: $event, distinct_id: $distinct_id, properties: $properties, timestamp: $timestamp) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="capture_event"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation capture_event: {str(e)}"
            )

    async def person_update(
        self,
        id: str,
        properties: Dict[str, Any]
    ) -> PostHogResponse:
        """Update person properties

        GraphQL Operation: Mutation

        Args:
            id (str, required): Parameter for id
            properties (Dict[str, Any], required): Parameter for properties

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if properties is not None:
            variables["properties"] = properties

        query = '''mutation PersonUpdate($id: ID!, $properties: JSON!) {
                            personUpdate(id: $id, properties: $properties) {
                                person {
                                    id
                                    name
                                    properties
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="person_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation person_update: {str(e)}"
            )

    async def person_delete(
        self,
        id: str
    ) -> PostHogResponse:
        """Delete a person

        GraphQL Operation: Mutation

        Args:
            id (str, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation PersonDelete($id: ID!) {
                            personDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="person_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation person_delete: {str(e)}"
            )

    async def action_create(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        description: Optional[str] = None
    ) -> PostHogResponse:
        """Create a new action

        GraphQL Operation: Mutation

        Args:
            name (str, required): Parameter for name
            steps (List[Dict[str, Any]], required): Parameter for steps
            description (str, optional): Parameter for description

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if name is not None:
            variables["name"] = name
        if steps is not None:
            variables["steps"] = steps
        if description is not None:
            variables["description"] = description

        query = '''mutation ActionCreate($name: String!, $steps: [JSON!]!, $description: String) {
                            actionCreate(name: $name, steps: $steps, description: $description) {
                                action {
                                    id
                                    name
                                    description
                                    steps {
                                        event
                                        url
                                        selector
                                    }
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="action_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation action_create: {str(e)}"
            )

    async def action_update(
        self,
        id: int,
        name: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        description: Optional[str] = None
    ) -> PostHogResponse:
        """Update an action

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            steps (List[Dict[str, Any]], optional): Parameter for steps
            description (str, optional): Parameter for description

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if steps is not None:
            variables["steps"] = steps
        if description is not None:
            variables["description"] = description

        query = '''mutation ActionUpdate($id: Int!, $name: String, $steps: [JSON!], $description: String) {
                            actionUpdate(id: $id, name: $name, steps: $steps, description: $description) {
                                action {
                                    id
                                    name
                                    description
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="action_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation action_update: {str(e)}"
            )

    async def action_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete an action

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation ActionDelete($id: Int!) {
                            actionDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="action_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation action_delete: {str(e)}"
            )

    async def cohort_create(
        self,
        name: str,
        filters: Dict[str, Any],
        is_static: Optional[bool] = None
    ) -> PostHogResponse:
        """Create a new cohort

        GraphQL Operation: Mutation

        Args:
            name (str, required): Parameter for name
            filters (Dict[str, Any], required): Parameter for filters
            is_static (bool, optional): Parameter for is_static

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters
        if is_static is not None:
            variables["is_static"] = is_static

        query = '''mutation CohortCreate($name: String!, $filters: JSON!, $is_static: Boolean) {
                            cohortCreate(name: $name, filters: $filters, is_static: $is_static) {
                                cohort {
                                    id
                                    name
                                    filters
                                    is_static
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="cohort_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation cohort_create: {str(e)}"
            )

    async def cohort_update(
        self,
        id: int,
        name: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> PostHogResponse:
        """Update a cohort

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            filters (Dict[str, Any], optional): Parameter for filters

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters

        query = '''mutation CohortUpdate($id: Int!, $name: String, $filters: JSON) {
                            cohortUpdate(id: $id, name: $name, filters: $filters) {
                                cohort {
                                    id
                                    name
                                    filters
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="cohort_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation cohort_update: {str(e)}"
            )

    async def cohort_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete a cohort

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation CohortDelete($id: Int!) {
                            cohortDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="cohort_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation cohort_delete: {str(e)}"
            )

    async def dashboard_create(
        self,
        name: str,
        description: Optional[str] = None,
        pinned: Optional[bool] = None
    ) -> PostHogResponse:
        """Create a new dashboard

        GraphQL Operation: Mutation

        Args:
            name (str, required): Parameter for name
            description (str, optional): Parameter for description
            pinned (bool, optional): Parameter for pinned

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if name is not None:
            variables["name"] = name
        if description is not None:
            variables["description"] = description
        if pinned is not None:
            variables["pinned"] = pinned

        query = '''mutation DashboardCreate($name: String!, $description: String, $pinned: Boolean) {
                            dashboardCreate(name: $name, description: $description, pinned: $pinned) {
                                dashboard {
                                    id
                                    name
                                    description
                                    pinned
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="dashboard_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation dashboard_create: {str(e)}"
            )

    async def dashboard_update(
        self,
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        pinned: Optional[bool] = None
    ) -> PostHogResponse:
        """Update a dashboard

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            description (str, optional): Parameter for description
            pinned (bool, optional): Parameter for pinned

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if description is not None:
            variables["description"] = description
        if pinned is not None:
            variables["pinned"] = pinned

        query = '''mutation DashboardUpdate($id: Int!, $name: String, $description: String, $pinned: Boolean) {
                            dashboardUpdate(id: $id, name: $name, description: $description, pinned: $pinned) {
                                dashboard {
                                    id
                                    name
                                    description
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="dashboard_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation dashboard_update: {str(e)}"
            )

    async def dashboard_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete a dashboard

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation DashboardDelete($id: Int!) {
                            dashboardDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="dashboard_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation dashboard_delete: {str(e)}"
            )

    async def insight_create(
        self,
        name: str,
        filters: Dict[str, Any],
        dashboard: Optional[int] = None,
        description: Optional[str] = None
    ) -> PostHogResponse:
        """Create a new insight

        GraphQL Operation: Mutation

        Args:
            name (str, required): Parameter for name
            filters (Dict[str, Any], required): Parameter for filters
            dashboard (int, optional): Parameter for dashboard
            description (str, optional): Parameter for description

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters
        if dashboard is not None:
            variables["dashboard"] = dashboard
        if description is not None:
            variables["description"] = description

        query = '''mutation InsightCreate($name: String!, $filters: JSON!, $dashboard: Int, $description: String) {
                            insightCreate(name: $name, filters: $filters, dashboard: $dashboard, description: $description) {
                                insight {
                                    id
                                    name
                                    filters
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="insight_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation insight_create: {str(e)}"
            )

    async def insight_update(
        self,
        id: int,
        name: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None
    ) -> PostHogResponse:
        """Update an insight

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            filters (Dict[str, Any], optional): Parameter for filters
            description (str, optional): Parameter for description

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters
        if description is not None:
            variables["description"] = description

        query = '''mutation InsightUpdate($id: Int!, $name: String, $filters: JSON, $description: String) {
                            insightUpdate(id: $id, name: $name, filters: $filters, description: $description) {
                                insight {
                                    id
                                    name
                                    filters
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="insight_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation insight_update: {str(e)}"
            )

    async def insight_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete an insight

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation InsightDelete($id: Int!) {
                            insightDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="insight_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation insight_delete: {str(e)}"
            )

    async def feature_flag_create(
        self,
        key: str,
        name: str,
        filters: Dict[str, Any],
        active: Optional[bool] = None
    ) -> PostHogResponse:
        """Create a new feature flag

        GraphQL Operation: Mutation

        Args:
            key (str, required): Parameter for key
            name (str, required): Parameter for name
            filters (Dict[str, Any], required): Parameter for filters
            active (bool, optional): Parameter for active

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if key is not None:
            variables["key"] = key
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters
        if active is not None:
            variables["active"] = active

        query = '''mutation FeatureFlagCreate($key: String!, $name: String!, $filters: JSON!, $active: Boolean) {
                            featureFlagCreate(key: $key, name: $name, filters: $filters, active: $active) {
                                featureFlag {
                                    id
                                    key
                                    name
                                    filters
                                    active
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="feature_flag_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation feature_flag_create: {str(e)}"
            )

    async def feature_flag_update(
        self,
        id: int,
        name: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        active: Optional[bool] = None
    ) -> PostHogResponse:
        """Update a feature flag

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            filters (Dict[str, Any], optional): Parameter for filters
            active (bool, optional): Parameter for active

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if filters is not None:
            variables["filters"] = filters
        if active is not None:
            variables["active"] = active

        query = '''mutation FeatureFlagUpdate($id: Int!, $name: String, $filters: JSON, $active: Boolean) {
                            featureFlagUpdate(id: $id, name: $name, filters: $filters, active: $active) {
                                featureFlag {
                                    id
                                    name
                                    filters
                                    active
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="feature_flag_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation feature_flag_update: {str(e)}"
            )

    async def feature_flag_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete a feature flag

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation FeatureFlagDelete($id: Int!) {
                            featureFlagDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="feature_flag_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation feature_flag_delete: {str(e)}"
            )

    async def experiment_create(
        self,
        name: str,
        feature_flag: int,
        parameters: Dict[str, Any]
    ) -> PostHogResponse:
        """Create a new experiment

        GraphQL Operation: Mutation

        Args:
            name (str, required): Parameter for name
            feature_flag (int, required): Parameter for feature_flag
            parameters (Dict[str, Any], required): Parameter for parameters

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if name is not None:
            variables["name"] = name
        if feature_flag is not None:
            variables["feature_flag"] = feature_flag
        if parameters is not None:
            variables["parameters"] = parameters

        query = '''mutation ExperimentCreate($name: String!, $feature_flag: Int!, $parameters: JSON!) {
                            experimentCreate(name: $name, feature_flag: $feature_flag, parameters: $parameters) {
                                experiment {
                                    id
                                    name
                                    feature_flag
                                    parameters
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="experiment_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation experiment_create: {str(e)}"
            )

    async def experiment_update(
        self,
        id: int,
        name: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None
    ) -> PostHogResponse:
        """Update an experiment

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            name (str, optional): Parameter for name
            parameters (Dict[str, Any], optional): Parameter for parameters

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if name is not None:
            variables["name"] = name
        if parameters is not None:
            variables["parameters"] = parameters

        query = '''mutation ExperimentUpdate($id: Int!, $name: String, $parameters: JSON) {
                            experimentUpdate(id: $id, name: $name, parameters: $parameters) {
                                experiment {
                                    id
                                    name
                                    parameters
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="experiment_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation experiment_update: {str(e)}"
            )

    async def experiment_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete an experiment

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation ExperimentDelete($id: Int!) {
                            experimentDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="experiment_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation experiment_delete: {str(e)}"
            )

    async def annotation_create(
        self,
        content: str,
        date_marker: str,
        dashboard_item: Optional[int] = None
    ) -> PostHogResponse:
        """Create an annotation

        GraphQL Operation: Mutation

        Args:
            content (str, required): Parameter for content
            date_marker (str, required): Parameter for date_marker
            dashboard_item (int, optional): Parameter for dashboard_item

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if content is not None:
            variables["content"] = content
        if date_marker is not None:
            variables["date_marker"] = date_marker
        if dashboard_item is not None:
            variables["dashboard_item"] = dashboard_item

        query = '''mutation AnnotationCreate($content: String!, $date_marker: String!, $dashboard_item: Int) {
                            annotationCreate(content: $content, date_marker: $date_marker, dashboard_item: $dashboard_item) {
                                annotation {
                                    id
                                    content
                                    date_marker
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="annotation_create"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation annotation_create: {str(e)}"
            )

    async def annotation_update(
        self,
        id: int,
        content: Optional[str] = None
    ) -> PostHogResponse:
        """Update an annotation

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id
            content (str, optional): Parameter for content

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id
        if content is not None:
            variables["content"] = content

        query = '''mutation AnnotationUpdate($id: Int!, $content: String) {
                            annotationUpdate(id: $id, content: $content) {
                                annotation {
                                    id
                                    content
                                }
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="annotation_update"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation annotation_update: {str(e)}"
            )

    async def annotation_delete(
        self,
        id: int
    ) -> PostHogResponse:
        """Delete an annotation

        GraphQL Operation: Mutation

        Args:
            id (int, required): Parameter for id

        Returns:
            PostHogResponse: The GraphQL response
        """
        variables = {}
        if id is not None:
            variables["id"] = id

        query = '''mutation AnnotationDelete($id: Int!) {
                            annotationDelete(id: $id) {
                                success
                            }
                        }'''

        try:
            response = await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name="annotation_delete"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute mutation annotation_delete: {str(e)}"
            )

