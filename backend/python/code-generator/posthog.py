#!/usr/bin/env python3
# ruff: noqa
"""
PostHog GraphQL Data Source Generator
Generates comprehensive wrapper methods for PostHog GraphQL API operations.
Creates a complete PostHog data source with analytics, events, and insights operations.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


class PostHogDataSourceGenerator:
    """Generate PostHog data source methods from GraphQL operations."""
    
    def __init__(self):
        """Initialize the PostHog data source generator."""
        self.generated_methods = []
        self.type_mappings = self._create_type_mappings()
        self.comprehensive_operations = self._define_comprehensive_operations()
        
    def _create_type_mappings(self) -> Dict[str, str]:
        """Create mappings from GraphQL types to Python types."""
        return {
            # Basic types
            'String': 'str',
            'Int': 'int',
            'Float': 'float',
            'Boolean': 'bool',
            'ID': 'str',
            'JSON': 'Dict[str, Any]',
            
            # PostHog specific types
            'Person': 'Dict[str, Any]',
            'Event': 'Dict[str, Any]',
            'Action': 'Dict[str, Any]',
            'Cohort': 'Dict[str, Any]',
            'Dashboard': 'Dict[str, Any]',
            'Insight': 'Dict[str, Any]',
            'FeatureFlag': 'Dict[str, Any]',
            'Experiment': 'Dict[str, Any]',
            'Annotation': 'Dict[str, Any]',
            'Team': 'Dict[str, Any]',
            'Organization': 'Dict[str, Any]',
            'User': 'Dict[str, Any]',
            'Plugin': 'Dict[str, Any]',
            'SessionRecording': 'Dict[str, Any]',
            
            # Input types
            'EventFilter': 'Dict[str, Any]',
            'PropertyFilter': 'Dict[str, Any]',
            'DateRange': 'Dict[str, Any]',
            'TrendFilter': 'Dict[str, Any]',
            'FunnelFilter': 'Dict[str, Any]',
            
            # Collections
            'List[Event]': 'List[Dict[str, Any]]',
            'List[Person]': 'List[Dict[str, Any]]',
            'List[Action]': 'List[Dict[str, Any]]',
            
            # Optional types
            'Optional[String]': 'Optional[str]',
            'Optional[Int]': 'Optional[int]',
            'Optional[Boolean]': 'Optional[bool]',
            
            # Response wrapper
            'PostHogResponse': 'PostHogResponse'
        }

    def _define_comprehensive_operations(self) -> Dict[str, Dict[str, Any]]:
        """Define comprehensive PostHog operations based on actual API with GraphQL queries."""
        return {
            # ================= QUERY OPERATIONS =================
            'queries': {
                # Event Queries
                'events': {
                    'description': 'Query events with filtering and pagination',
                    'parameters': {
                        'after': {'type': 'str', 'required': False},
                        'before': {'type': 'str', 'required': False},
                        'distinct_id': {'type': 'str', 'required': False},
                        'event': {'type': 'str', 'required': False},
                        'properties': {'type': 'Dict[str, Any]', 'required': False},
                        'limit': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Events($after: String, $before: String, $distinct_id: String, $event: String, $properties: JSON, $limit: Int) {
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
                        }
                    '''
                },
                'event': {
                    'description': 'Get single event by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'query': '''
                        query Event($id: ID!) {
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
                        }
                    '''
                },
                
                # Person Queries
                'persons': {
                    'description': 'Query persons with filtering',
                    'parameters': {
                        'search': {'type': 'str', 'required': False},
                        'properties': {'type': 'Dict[str, Any]', 'required': False},
                        'cohort': {'type': 'int', 'required': False},
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Persons($search: String, $properties: JSON, $cohort: Int, $limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'person': {
                    'description': 'Get person by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'query': '''
                        query Person($id: ID!) {
                            person(id: $id) {
                                id
                                name
                                distinct_ids
                                properties
                                created_at
                                updated_at
                            }
                        }
                    '''
                },
                
                # Action Queries
                'actions': {
                    'description': 'Get all actions',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Actions($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'action': {
                    'description': 'Get action by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Action($id: Int!) {
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
                        }
                    '''
                },
                
                # Cohort Queries
                'cohorts': {
                    'description': 'Get all cohorts',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Cohorts($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'cohort': {
                    'description': 'Get cohort by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Cohort($id: Int!) {
                            cohort(id: $id) {
                                id
                                name
                                description
                                is_static
                                filters
                                count
                                created_at
                            }
                        }
                    '''
                },
                
                # Dashboard Queries
                'dashboards': {
                    'description': 'Get all dashboards',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Dashboards($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'dashboard': {
                    'description': 'Get dashboard by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Dashboard($id: Int!) {
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
                        }
                    '''
                },
                
                # Insight Queries
                'insights': {
                    'description': 'Get insights with filtering',
                    'parameters': {
                        'dashboard': {'type': 'int', 'required': False},
                        'saved': {'type': 'bool', 'required': False},
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Insights($dashboard: Int, $saved: Boolean, $limit: Int, $offset: Int) {
                            insights(dashboard: $dashboard, saved: $saved, limit: $limit, offset: $offset) {
                                results {
                                    id
                                    name
                                    filters
                                    result
                                    created_at
                                }
                            }
                        }
                    '''
                },
                'insight': {
                    'description': 'Get insight by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Insight($id: Int!) {
                            insight(id: $id) {
                                id
                                name
                                filters
                                result
                                created_at
                            }
                        }
                    '''
                },
                'trend': {
                    'description': 'Calculate trends for events',
                    'parameters': {
                        'events': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'date_from': {'type': 'str', 'required': False},
                        'date_to': {'type': 'str', 'required': False},
                        'interval': {'type': 'str', 'required': False},
                        'properties': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'query': '''
                        query Trend($events: [JSON!]!, $date_from: String, $date_to: String, $interval: String, $properties: JSON) {
                            trend(events: $events, date_from: $date_from, date_to: $date_to, interval: $interval, properties: $properties) {
                                result {
                                    labels
                                    data
                                    count
                                }
                            }
                        }
                    '''
                },
                'funnel': {
                    'description': 'Calculate funnel for event sequence',
                    'parameters': {
                        'events': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'date_from': {'type': 'str', 'required': False},
                        'date_to': {'type': 'str', 'required': False},
                        'funnel_window_interval': {'type': 'int', 'required': False},
                        'funnel_window_interval_unit': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        query Funnel($events: [JSON!]!, $date_from: String, $date_to: String, $funnel_window_interval: Int, $funnel_window_interval_unit: String) {
                            funnel(events: $events, date_from: $date_from, date_to: $date_to, funnel_window_interval: $funnel_window_interval, funnel_window_interval_unit: $funnel_window_interval_unit) {
                                result {
                                    steps {
                                        name
                                        count
                                        average_conversion_time
                                    }
                                }
                            }
                        }
                    '''
                },
                
                # Feature Flag Queries
                'feature_flags': {
                    'description': 'Get all feature flags',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query FeatureFlags($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'feature_flag': {
                    'description': 'Get feature flag by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query FeatureFlag($id: Int!) {
                            featureFlag(id: $id) {
                                id
                                key
                                name
                                filters
                                active
                                created_at
                            }
                        }
                    '''
                },
                
                # Experiment Queries
                'experiments': {
                    'description': 'Get all experiments',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Experiments($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'experiment': {
                    'description': 'Get experiment by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Experiment($id: Int!) {
                            experiment(id: $id) {
                                id
                                name
                                feature_flag
                                parameters
                                start_date
                                end_date
                            }
                        }
                    '''
                },
                
                # Session Recording Queries
                'session_recordings': {
                    'description': 'Get session recordings',
                    'parameters': {
                        'person_id': {'type': 'str', 'required': False},
                        'date_from': {'type': 'str', 'required': False},
                        'date_to': {'type': 'str', 'required': False},
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query SessionRecordings($person_id: String, $date_from: String, $date_to: String, $limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'session_recording': {
                    'description': 'Get session recording by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'query': '''
                        query SessionRecording($id: ID!) {
                            sessionRecording(id: $id) {
                                id
                                distinct_id
                                viewed
                                recording_duration
                                start_time
                                end_time
                                snapshot_data
                            }
                        }
                    '''
                },
                
                # Organization & Team Queries
                'organization': {
                    'description': 'Get current organization',
                    'parameters': {},
                    'query': '''
                        query Organization {
                            organization {
                                id
                                name
                                created_at
                                updated_at
                                membership_level
                            }
                        }
                    '''
                },
                'team': {
                    'description': 'Get current team',
                    'parameters': {},
                    'query': '''
                        query Team {
                            team {
                                id
                                name
                                created_at
                                updated_at
                            }
                        }
                    '''
                },
                
                # Plugin Queries
                'plugins': {
                    'description': 'Get all plugins',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        query Plugins($limit: Int, $offset: Int) {
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
                        }
                    '''
                },
                'plugin': {
                    'description': 'Get plugin by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        query Plugin($id: Int!) {
                            plugin(id: $id) {
                                id
                                name
                                description
                                url
                                config_schema
                                enabled
                            }
                        }
                    '''
                }
            },
            
            # ================= MUTATION OPERATIONS =================
            'mutations': {
                # Event Mutations
                'capture_event': {
                    'description': 'Capture a new event',
                    'parameters': {
                        'event': {'type': 'str', 'required': True},
                        'distinct_id': {'type': 'str', 'required': True},
                        'properties': {'type': 'Dict[str, Any]', 'required': False},
                        'timestamp': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation CaptureEvent($event: String!, $distinct_id: String!, $properties: JSON, $timestamp: String) {
                            captureEvent(event: $event, distinct_id: $distinct_id, properties: $properties, timestamp: $timestamp) {
                                success
                            }
                        }
                    '''
                },
                
                # Person Mutations
                'person_update': {
                    'description': 'Update person properties',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'properties': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'query': '''
                        mutation PersonUpdate($id: ID!, $properties: JSON!) {
                            personUpdate(id: $id, properties: $properties) {
                                person {
                                    id
                                    name
                                    properties
                                }
                            }
                        }
                    '''
                },
                'person_delete': {
                    'description': 'Delete a person',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    },
                    'query': '''
                        mutation PersonDelete($id: ID!) {
                            personDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Action Mutations
                'action_create': {
                    'description': 'Create a new action',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'steps': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'description': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation ActionCreate($name: String!, $steps: [JSON!]!, $description: String) {
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
                        }
                    '''
                },
                'action_update': {
                    'description': 'Update an action',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'steps': {'type': 'List[Dict[str, Any]]', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation ActionUpdate($id: Int!, $name: String, $steps: [JSON!], $description: String) {
                            actionUpdate(id: $id, name: $name, steps: $steps, description: $description) {
                                action {
                                    id
                                    name
                                    description
                                }
                            }
                        }
                    '''
                },
                'action_delete': {
                    'description': 'Delete an action',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation ActionDelete($id: Int!) {
                            actionDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Cohort Mutations
                'cohort_create': {
                    'description': 'Create a new cohort',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'is_static': {'type': 'bool', 'required': False}
                    },
                    'query': '''
                        mutation CohortCreate($name: String!, $filters: JSON!, $is_static: Boolean) {
                            cohortCreate(name: $name, filters: $filters, is_static: $is_static) {
                                cohort {
                                    id
                                    name
                                    filters
                                    is_static
                                }
                            }
                        }
                    '''
                },
                'cohort_update': {
                    'description': 'Update a cohort',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'query': '''
                        mutation CohortUpdate($id: Int!, $name: String, $filters: JSON) {
                            cohortUpdate(id: $id, name: $name, filters: $filters) {
                                cohort {
                                    id
                                    name
                                    filters
                                }
                            }
                        }
                    '''
                },
                'cohort_delete': {
                    'description': 'Delete a cohort',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation CohortDelete($id: Int!) {
                            cohortDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Dashboard Mutations
                'dashboard_create': {
                    'description': 'Create a new dashboard',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'description': {'type': 'str', 'required': False},
                        'pinned': {'type': 'bool', 'required': False}
                    },
                    'query': '''
                        mutation DashboardCreate($name: String!, $description: String, $pinned: Boolean) {
                            dashboardCreate(name: $name, description: $description, pinned: $pinned) {
                                dashboard {
                                    id
                                    name
                                    description
                                    pinned
                                }
                            }
                        }
                    '''
                },
                'dashboard_update': {
                    'description': 'Update a dashboard',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'description': {'type': 'str', 'required': False},
                        'pinned': {'type': 'bool', 'required': False}
                    },
                    'query': '''
                        mutation DashboardUpdate($id: Int!, $name: String, $description: String, $pinned: Boolean) {
                            dashboardUpdate(id: $id, name: $name, description: $description, pinned: $pinned) {
                                dashboard {
                                    id
                                    name
                                    description
                                }
                            }
                        }
                    '''
                },
                'dashboard_delete': {
                    'description': 'Delete a dashboard',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation DashboardDelete($id: Int!) {
                            dashboardDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Insight Mutations
                'insight_create': {
                    'description': 'Create a new insight',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'dashboard': {'type': 'int', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation InsightCreate($name: String!, $filters: JSON!, $dashboard: Int, $description: String) {
                            insightCreate(name: $name, filters: $filters, dashboard: $dashboard, description: $description) {
                                insight {
                                    id
                                    name
                                    filters
                                }
                            }
                        }
                    '''
                },
                'insight_update': {
                    'description': 'Update an insight',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation InsightUpdate($id: Int!, $name: String, $filters: JSON, $description: String) {
                            insightUpdate(id: $id, name: $name, filters: $filters, description: $description) {
                                insight {
                                    id
                                    name
                                    filters
                                }
                            }
                        }
                    '''
                },
                'insight_delete': {
                    'description': 'Delete an insight',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation InsightDelete($id: Int!) {
                            insightDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Feature Flag Mutations
                'feature_flag_create': {
                    'description': 'Create a new feature flag',
                    'parameters': {
                        'key': {'type': 'str', 'required': True},
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'active': {'type': 'bool', 'required': False}
                    },
                    'query': '''
                        mutation FeatureFlagCreate($key: String!, $name: String!, $filters: JSON!, $active: Boolean) {
                            featureFlagCreate(key: $key, name: $name, filters: $filters, active: $active) {
                                featureFlag {
                                    id
                                    key
                                    name
                                    filters
                                    active
                                }
                            }
                        }
                    '''
                },
                'feature_flag_update': {
                    'description': 'Update a feature flag',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False},
                        'active': {'type': 'bool', 'required': False}
                    },
                    'query': '''
                        mutation FeatureFlagUpdate($id: Int!, $name: String, $filters: JSON, $active: Boolean) {
                            featureFlagUpdate(id: $id, name: $name, filters: $filters, active: $active) {
                                featureFlag {
                                    id
                                    name
                                    filters
                                    active
                                }
                            }
                        }
                    '''
                },
                'feature_flag_delete': {
                    'description': 'Delete a feature flag',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation FeatureFlagDelete($id: Int!) {
                            featureFlagDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Experiment Mutations
                'experiment_create': {
                    'description': 'Create a new experiment',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'feature_flag': {'type': 'int', 'required': True},
                        'parameters': {'type': 'Dict[str, Any]', 'required': True}
                    },
                    'query': '''
                        mutation ExperimentCreate($name: String!, $feature_flag: Int!, $parameters: JSON!) {
                            experimentCreate(name: $name, feature_flag: $feature_flag, parameters: $parameters) {
                                experiment {
                                    id
                                    name
                                    feature_flag
                                    parameters
                                }
                            }
                        }
                    '''
                },
                'experiment_update': {
                    'description': 'Update an experiment',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'parameters': {'type': 'Dict[str, Any]', 'required': False}
                    },
                    'query': '''
                        mutation ExperimentUpdate($id: Int!, $name: String, $parameters: JSON) {
                            experimentUpdate(id: $id, name: $name, parameters: $parameters) {
                                experiment {
                                    id
                                    name
                                    parameters
                                }
                            }
                        }
                    '''
                },
                'experiment_delete': {
                    'description': 'Delete an experiment',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation ExperimentDelete($id: Int!) {
                            experimentDelete(id: $id) {
                                success
                            }
                        }
                    '''
                },
                
                # Annotation Mutations
                'annotation_create': {
                    'description': 'Create an annotation',
                    'parameters': {
                        'content': {'type': 'str', 'required': True},
                        'date_marker': {'type': 'str', 'required': True},
                        'dashboard_item': {'type': 'int', 'required': False}
                    },
                    'query': '''
                        mutation AnnotationCreate($content: String!, $date_marker: String!, $dashboard_item: Int) {
                            annotationCreate(content: $content, date_marker: $date_marker, dashboard_item: $dashboard_item) {
                                annotation {
                                    id
                                    content
                                    date_marker
                                }
                            }
                        }
                    '''
                },
                'annotation_update': {
                    'description': 'Update an annotation',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'content': {'type': 'str', 'required': False}
                    },
                    'query': '''
                        mutation AnnotationUpdate($id: Int!, $content: String) {
                            annotationUpdate(id: $id, content: $content) {
                                annotation {
                                    id
                                    content
                                }
                            }
                        }
                    '''
                },
                'annotation_delete': {
                    'description': 'Delete an annotation',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    },
                    'query': '''
                        mutation AnnotationDelete($id: Int!) {
                            annotationDelete(id: $id) {
                                success
                            }
                        }
                    '''
                }
            }
        }
    
    def _generate_method_signature(self, operation_name: str, operation_data: Dict[str, Any]) -> Tuple[str, str]:
        """Generate method signature."""
        parameters = operation_data.get('parameters', {})
        
        required_params = []
        optional_params = []
        
        for param_name, param_info in parameters.items():
            param_type = param_info['type']
            
            if param_info.get('required', False):
                required_params.append(f"{param_name}: {param_type}")
            else:
                if param_type.startswith('Optional['):
                    optional_params.append(f"{param_name}: {param_type} = None")
                else:
                    optional_params.append(f"{param_name}: Optional[{param_type}] = None")
        
        all_params = ['self'] + required_params + optional_params
        
        if len(all_params) == 1:
            signature = f"async def {operation_name}(self) -> PostHogResponse:"
        else:
            params_formatted = ',\n        '.join(all_params)
            signature = f"async def {operation_name}(\n        {params_formatted}\n    ) -> PostHogResponse:"
        
        return signature, operation_name
    
    def _generate_docstring(self, operation_name: str, operation_data: Dict[str, Any], operation_type: str) -> str:
        """Generate method docstring."""
        description = operation_data.get('description', f'PostHog {operation_type}: {operation_name}')
        parameters = operation_data.get('parameters', {})
        
        docstring = f'        """{description}\n\n'
        docstring += f'        GraphQL Operation: {operation_type.title()}\n'
        
        if parameters:
            docstring += '\n        Args:\n'
            for param_name, param_info in parameters.items():
                param_type = param_info['type']
                required_text = 'required' if param_info.get('required', False) else 'optional'
                docstring += f'            {param_name} ({param_type}, {required_text}): Parameter for {param_name}\n'
        
        docstring += f'\n        Returns:\n            PostHogResponse: The GraphQL response\n'
        docstring += '        """'
        return docstring
    
    async def _execute_operation(
        self,
        query: str,
        variables: Dict[str, Any],
        operation_name: str,
        operation_type: str,
    ) -> PostHogResponse:
        """Execute a GraphQL operation and handle exceptions."""
        try:
            return await self._posthog_client.execute_query(
                query=query,
                variables=variables,
                operation_name=operation_name,
            )
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute {operation_type} {operation_name}: {str(e)}",
            )

    
    def _generate_method_body(self, operation_name: str, operation_data: Dict[str, Any], operation_type: str) -> str:
        """Generate method body."""
        parameters = operation_data.get("parameters", {})
        query = operation_data.get("query", "").strip()

        # Build variable setup
        if parameters:
            variables_lines = ['        variables = {}']
            for param_name in parameters.keys():
                variables_lines.append(f'        if {param_name} is not None:')
                variables_lines.append(f'            variables["{param_name}"] = {param_name}')
            variables_setup = "\n".join(variables_lines)
        else:
            variables_setup = "        variables = {}"

        # Use the actual GraphQL query from the operation definition
        query_str = query.replace('"""', '\\"\\"\\"')

        # Generate body that calls the new helper function
        return f"""{variables_setup}
            
            query = '''{query}'''

            return await self._execute_operation(
                query=query,
                variables=variables,
                operation_name="{operation_name}",
                operation_type="{operation_type}"
            )"""

    
    def generate_datasource(self) -> str:
        """Generate complete PostHog data source class."""
        
        queries = self.comprehensive_operations['queries']
        mutations = self.comprehensive_operations['mutations']
        
        class_code = '''from typing import Dict, List, Optional, Any

from app.sources.client.posthog.posthog import (
    PostHogClient,
    PostHogResponse
)


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

'''
        
        # Generate query methods
        for op_name, op_data in queries.items():
            signature, method_name = self._generate_method_signature(op_name, op_data)
            docstring = self._generate_docstring(op_name, op_data, 'query')
            method_body = self._generate_method_body(op_name, op_data, 'query')
            
            class_code += f"    {signature}\n{docstring}\n{method_body}\n\n"
            
            self.generated_methods.append({
                'name': method_name,
                'type': 'query',
                'params': len(op_data.get('parameters', {}))
            })
        
        class_code += '''    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

'''
        
        # Generate mutation methods
        for op_name, op_data in mutations.items():
            signature, method_name = self._generate_method_signature(op_name, op_data)
            docstring = self._generate_docstring(op_name, op_data, 'mutation')
            method_body = self._generate_method_body(op_name, op_data, 'mutation')
            
            class_code += f"    {signature}\n{docstring}\n{method_body}\n\n"
            
            self.generated_methods.append({
                'name': method_name,
                'type': 'mutation',
                'params': len(op_data.get('parameters', {}))
            })
        
        return class_code
    
    def save_to_file(self, filename: Optional[str] = None):
        """Save generated code to file."""
        if filename is None:
            filename = "posthog_data_source.py"
        
        script_dir = Path(__file__).parent if __file__ else Path('.')
        posthog_dir = script_dir / 'posthog'
        posthog_dir.mkdir(exist_ok=True)
        
        full_path = posthog_dir / filename
        class_code = self.generate_datasource()
        full_path.write_text(class_code, encoding='utf-8')
        
        query_count = len([m for m in self.generated_methods if m['type'] == 'query'])
        mutation_count = len([m for m in self.generated_methods if m['type'] == 'mutation'])
        
        print(f"✅ Generated PostHog data source with {len(self.generated_methods)} methods")
        print(f"📁 Saved to: {full_path}")
        print(f"\n📊 Summary:")
        print(f"   - Total methods: {len(self.generated_methods)}")
        print(f"   - Query methods: {query_count}")
        print(f"   - Mutation methods: {mutation_count}")


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate PostHog data source')
    parser.add_argument('--filename', '-f', help='Output filename')
    args = parser.parse_args()
    
    try:
        generator = PostHogDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"❌ Failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())