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
        """Define comprehensive PostHog operations based on actual API."""
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
                    }
                },
                'event': {
                    'description': 'Get single event by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    }
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
                    }
                },
                'person': {
                    'description': 'Get person by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    }
                },
                
                # Action Queries
                'actions': {
                    'description': 'Get all actions',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'action': {
                    'description': 'Get action by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Cohort Queries
                'cohorts': {
                    'description': 'Get all cohorts',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'cohort': {
                    'description': 'Get cohort by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Dashboard Queries
                'dashboards': {
                    'description': 'Get all dashboards',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'dashboard': {
                    'description': 'Get dashboard by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Insight Queries
                'insights': {
                    'description': 'Get insights with filtering',
                    'parameters': {
                        'dashboard': {'type': 'int', 'required': False},
                        'saved': {'type': 'bool', 'required': False},
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'insight': {
                    'description': 'Get insight by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                'trend': {
                    'description': 'Calculate trends for events',
                    'parameters': {
                        'events': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'date_from': {'type': 'str', 'required': False},
                        'date_to': {'type': 'str', 'required': False},
                        'interval': {'type': 'str', 'required': False},
                        'properties': {'type': 'Dict[str, Any]', 'required': False}
                    }
                },
                'funnel': {
                    'description': 'Calculate funnel for event sequence',
                    'parameters': {
                        'events': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'date_from': {'type': 'str', 'required': False},
                        'date_to': {'type': 'str', 'required': False},
                        'funnel_window_interval': {'type': 'int', 'required': False},
                        'funnel_window_interval_unit': {'type': 'str', 'required': False}
                    }
                },
                
                # Feature Flag Queries
                'feature_flags': {
                    'description': 'Get all feature flags',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'feature_flag': {
                    'description': 'Get feature flag by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Experiment Queries
                'experiments': {
                    'description': 'Get all experiments',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'experiment': {
                    'description': 'Get experiment by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
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
                    }
                },
                'session_recording': {
                    'description': 'Get session recording by ID',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    }
                },
                
                # Organization & Team Queries
                'organization': {
                    'description': 'Get current organization',
                    'parameters': {}
                },
                'team': {
                    'description': 'Get current team',
                    'parameters': {}
                },
                
                # Plugin Queries
                'plugins': {
                    'description': 'Get all plugins',
                    'parameters': {
                        'limit': {'type': 'int', 'required': False},
                        'offset': {'type': 'int', 'required': False}
                    }
                },
                'plugin': {
                    'description': 'Get plugin by ID',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
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
                    }
                },
                
                # Person Mutations
                'person_update': {
                    'description': 'Update person properties',
                    'parameters': {
                        'id': {'type': 'str', 'required': True},
                        'properties': {'type': 'Dict[str, Any]', 'required': True}
                    }
                },
                'person_delete': {
                    'description': 'Delete a person',
                    'parameters': {
                        'id': {'type': 'str', 'required': True}
                    }
                },
                
                # Action Mutations
                'action_create': {
                    'description': 'Create a new action',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'steps': {'type': 'List[Dict[str, Any]]', 'required': True},
                        'description': {'type': 'str', 'required': False}
                    }
                },
                'action_update': {
                    'description': 'Update an action',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'steps': {'type': 'List[Dict[str, Any]]', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    }
                },
                'action_delete': {
                    'description': 'Delete an action',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Cohort Mutations
                'cohort_create': {
                    'description': 'Create a new cohort',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'is_static': {'type': 'bool', 'required': False}
                    }
                },
                'cohort_update': {
                    'description': 'Update a cohort',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False}
                    }
                },
                'cohort_delete': {
                    'description': 'Delete a cohort',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Dashboard Mutations
                'dashboard_create': {
                    'description': 'Create a new dashboard',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'description': {'type': 'str', 'required': False},
                        'pinned': {'type': 'bool', 'required': False}
                    }
                },
                'dashboard_update': {
                    'description': 'Update a dashboard',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'description': {'type': 'str', 'required': False},
                        'pinned': {'type': 'bool', 'required': False}
                    }
                },
                'dashboard_delete': {
                    'description': 'Delete a dashboard',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Insight Mutations
                'insight_create': {
                    'description': 'Create a new insight',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'dashboard': {'type': 'int', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    }
                },
                'insight_update': {
                    'description': 'Update an insight',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False},
                        'description': {'type': 'str', 'required': False}
                    }
                },
                'insight_delete': {
                    'description': 'Delete an insight',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Feature Flag Mutations
                'feature_flag_create': {
                    'description': 'Create a new feature flag',
                    'parameters': {
                        'key': {'type': 'str', 'required': True},
                        'name': {'type': 'str', 'required': True},
                        'filters': {'type': 'Dict[str, Any]', 'required': True},
                        'active': {'type': 'bool', 'required': False}
                    }
                },
                'feature_flag_update': {
                    'description': 'Update a feature flag',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'filters': {'type': 'Dict[str, Any]', 'required': False},
                        'active': {'type': 'bool', 'required': False}
                    }
                },
                'feature_flag_delete': {
                    'description': 'Delete a feature flag',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Experiment Mutations
                'experiment_create': {
                    'description': 'Create a new experiment',
                    'parameters': {
                        'name': {'type': 'str', 'required': True},
                        'feature_flag': {'type': 'int', 'required': True},
                        'parameters': {'type': 'Dict[str, Any]', 'required': True}
                    }
                },
                'experiment_update': {
                    'description': 'Update an experiment',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'name': {'type': 'str', 'required': False},
                        'parameters': {'type': 'Dict[str, Any]', 'required': False}
                    }
                },
                'experiment_delete': {
                    'description': 'Delete an experiment',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
                },
                
                # Annotation Mutations
                'annotation_create': {
                    'description': 'Create an annotation',
                    'parameters': {
                        'content': {'type': 'str', 'required': True},
                        'date_marker': {'type': 'str', 'required': True},
                        'dashboard_item': {'type': 'int', 'required': False}
                    }
                },
                'annotation_update': {
                    'description': 'Update an annotation',
                    'parameters': {
                        'id': {'type': 'int', 'required': True},
                        'content': {'type': 'str', 'required': False}
                    }
                },
                'annotation_delete': {
                    'description': 'Delete an annotation',
                    'parameters': {
                        'id': {'type': 'int', 'required': True}
                    }
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
    
    def _generate_method_body(self, operation_name: str, operation_data: Dict[str, Any], operation_type: str) -> str:
        """Generate method body."""
        parameters = operation_data.get('parameters', {})
        
        if parameters:
            variables_lines = ['        variables = {}']
            for param_name in parameters.keys():
                variables_lines.append(f'        if {param_name} is not None:')
                variables_lines.append(f'            variables["{param_name}"] = {param_name}')
            variables_setup = '\n'.join(variables_lines)
        else:
            variables_setup = '        variables = {}'
        
        return f"""{variables_setup}
        
        try:
            response = await self._posthog_client.execute_query(
                query=self._get_query("{operation_type}", "{operation_name}"),
                variables=variables,
                operation_name="{operation_name}"
            )
            return response
        except Exception as e:
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute {operation_type} {operation_name}: {{str(e)}}"
            )"""
    
    def generate_datasource(self) -> str:
        """Generate complete PostHog data source class."""
        
        queries = self.comprehensive_operations['queries']
        mutations = self.comprehensive_operations['mutations']
        
        class_code = '''from typing import Dict, List, Optional, Any

from app.sources.client.posthog.posthogclient import (
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
    
    def _get_query(self, operation_type: str, operation_name: str) -> str:
        """Get GraphQL query for operation."""
        # Implementation would return actual GraphQL queries
        return f"{operation_type} {operation_name}"

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