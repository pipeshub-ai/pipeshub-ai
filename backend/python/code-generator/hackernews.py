# ruff: noqa
"""
HackerNews API DataSource Generator

Generates a HackerNewsDataSource class covering the official HackerNews v0
API (Firebase-backed, public, read-only, no authentication):

- Items (stories, comments, jobs, polls, poll options)
- Users
- Live data: max item id, top/new/best/ask/show/job story id lists, updates

Official docs: https://github.com/HackerNews/API
"""

import argparse
import keyword
from pathlib import Path
from typing import Dict

# ============================================================================
# HACKERNEWS API ENDPOINT DEFINITIONS
# ============================================================================

HACKERNEWS_API_ENDPOINTS: Dict[str, Dict] = {
    'get_item': {
        'method': 'GET',
        'path': '/item/{item_id}.json',
        'description': (
            'Get an item (story, comment, job, poll, or poll option) by id.'
        ),
        'parameters': {
            'item_id': {'type': 'int', 'location': 'path', 'description': 'The item\'s unique id'},
        },
        'required': ['item_id'],
    },

    'get_user': {
        'method': 'GET',
        'path': '/user/{username}.json',
        'description': 'Get a user profile by username (HackerNews usernames are case-sensitive).',
        'parameters': {
            'username': {'type': 'str', 'location': 'path', 'description': 'The user\'s unique username'},
        },
        'required': ['username'],
    },

    'get_max_item_id': {
        'method': 'GET',
        'path': '/maxitem.json',
        'description': 'Get the current largest item id. Poll this to walk every item sequentially.',
        'parameters': {},
        'required': [],
    },

    'get_top_stories': {
        'method': 'GET',
        'path': '/topstories.json',
        'description': 'Get up to 500 of the current top story ids, best rank first.',
        'parameters': {},
        'required': [],
    },

    'get_new_stories': {
        'method': 'GET',
        'path': '/newstories.json',
        'description': 'Get up to 500 of the newest story ids, most recent first.',
        'parameters': {},
        'required': [],
    },

    'get_best_stories': {
        'method': 'GET',
        'path': '/beststories.json',
        'description': 'Get up to 500 of the current best story ids.',
        'parameters': {},
        'required': [],
    },

    'get_ask_stories': {
        'method': 'GET',
        'path': '/askstories.json',
        'description': 'Get up to 200 of the latest Ask HN story ids.',
        'parameters': {},
        'required': [],
    },

    'get_show_stories': {
        'method': 'GET',
        'path': '/showstories.json',
        'description': 'Get up to 200 of the latest Show HN story ids.',
        'parameters': {},
        'required': [],
    },

    'get_job_stories': {
        'method': 'GET',
        'path': '/jobstories.json',
        'description': 'Get up to 200 of the latest Job story ids.',
        'parameters': {},
        'required': [],
    },

    'get_updates': {
        'method': 'GET',
        'path': '/updates.json',
        'description': (
            'Get recently changed items and profiles, as {"items": [...ids], '
            '"profiles": [...usernames]}.'
        ),
        'parameters': {},
        'required': [],
    },
}

_PY_RESERVED = set(keyword.kwlist) | {"from", "global", "async", "await", "None", "self", "cls"}
_ALWAYS_RESERVED_NAMES = {"self", "headers", "body", "body_additional"}


def _sanitize_name(name: str) -> str:
    """Sanitize parameter names to be valid Python identifiers."""
    sanitized = name.replace('-', '_').replace('.', '_').replace('[]', '_array')

    if sanitized in _PY_RESERVED or sanitized in _ALWAYS_RESERVED_NAMES:
        sanitized = f"{sanitized}_param"

    if sanitized[0].isdigit():
        sanitized = f"param_{sanitized}"

    return sanitized


def _generate_method(method_name: str, endpoint_info: Dict) -> str:
    """Generate a single method for the HackerNewsDataSource class."""
    method = endpoint_info['method']
    path = endpoint_info['path']
    description = endpoint_info['description']
    parameters = endpoint_info.get('parameters', {})
    required = endpoint_info.get('required', [])

    path_params = []
    for param_name, param_info in parameters.items():
        path_params.append({
            'name': param_name,
            'sanitized': _sanitize_name(param_name),
            'type': param_info['type'],
            'description': param_info['description'],
            'required': param_name in required,
        })

    # Every HackerNews v0 endpoint is a parameterless GET or takes exactly
    # one required path parameter — no query/body/file params exist in this
    # API, so the generator does not need to handle those shapes at all.
    sig_parts = ['self'] + [f"{p['sanitized']}: {p['type']}" for p in path_params]
    signature = (
        f"    async def {method_name}(\n        " + ",\n        ".join(sig_parts)
        + "\n    ) -> HackerNewsResponse:"
    )

    docstring_lines = [f'        """{description}']
    if path_params:
        docstring_lines.append('')
        docstring_lines.append('        Args:')
        for param in path_params:
            docstring_lines.append(f"            {param['sanitized']}: {param['description']} (required)")
    docstring_lines.append('')
    docstring_lines.append('        Returns:')
    docstring_lines.append('            HackerNewsResponse: Response object with success status and data/error')
    docstring_lines.append('        """')
    docstring = '\n'.join(docstring_lines)

    body_lines = []
    if path_params:
        format_args = ', '.join([f'{p["name"]}={p["sanitized"]}' for p in path_params])
        body_lines.append(f'        url = self.base_url + "{path}".format({format_args})')
    else:
        body_lines.append(f'        url = self.base_url + "{path}"')

    body_lines.append('')
    body_lines.append('        headers = dict(self.http.headers)')
    body_lines.append('')
    body_lines.append('        request = HTTPRequest(')
    body_lines.append(f'            method="{method}",')
    body_lines.append('            url=url,')
    body_lines.append('            headers=headers,')
    body_lines.append('            query_params={},')
    body_lines.append('            body=None')
    body_lines.append('        )')
    body_lines.append('')
    body_lines.append('        try:')
    body_lines.append('            response = await self.http.execute(request)')
    body_lines.append('            if response.status >= 400:')
    body_lines.append('                return HackerNewsResponse(')
    body_lines.append('                    success=False,')
    body_lines.append('                    error=f"HTTP {response.status}: {response.text()}",')
    body_lines.append('                )')
    body_lines.append('            return HackerNewsResponse(success=True, data=response.json())')
    body_lines.append('        except Exception as e:')
    body_lines.append('            return HackerNewsResponse(success=False, error=str(e))')

    return signature + '\n' + docstring + '\n' + '\n'.join(body_lines)


def generate_hackernews_datasource() -> str:
    """Generate the complete HackerNewsDataSource class."""

    lines = [
        '"""',
        'HackerNews API DataSource',
        '',
        'Auto-generated HackerNews (Firebase v0) API client wrapper.',
        'Covers the full official HackerNews API with explicit type hints.',
        '',
        'Generated from HackerNews API documentation at:',
        'https://github.com/HackerNews/API',
        '"""',
        '',
        'from app.sources.client.hackernews.hackernews import HackerNewsClient, HackerNewsResponse',
        'from app.sources.client.http.http_request import HTTPRequest',
        '',
        '',
        'class HackerNewsDataSource:',
        '    """Comprehensive HackerNews API client wrapper.',
        '    ',
        '    Provides async methods for the full official HackerNews v0 API:',
        '    ',
        '    ITEMS & USERS:',
        '    - get_item, get_user',
        '    ',
        '    LIVE DATA:',
        '    - get_max_item_id, get_top_stories, get_new_stories, get_best_stories,',
        '      get_ask_stories, get_show_stories, get_job_stories, get_updates',
        '    ',
        '    All methods return HackerNewsResponse objects with a standardized',
        '    success/data/error shape. No Any types — all parameters are',
        '    explicitly typed. The API is public and read-only: no auth is sent.',
        '    """',
        '',
        '    def __init__(self, client: HackerNewsClient) -> None:',
        '        """Initialize with HackerNewsClient.',
        '        ',
        '        Args:',
        '            client: HackerNewsClient instance',
        '        """',
        '        self._client = client',
        '        self.http = client.get_client()',
        '        if self.http is None:',
        "            raise ValueError('HTTP client is not initialized')",
        '        try:',
        "            self.base_url = self.http.get_base_url().rstrip('/')",
        '        except AttributeError as exc:',
        "            raise ValueError('HTTP client does not have get_base_url method') from exc",
        '',
        "    def get_data_source(self) -> 'HackerNewsDataSource':",
        '        """Return the data source instance."""',
        '        return self',
        '',
    ]

    for method_name, endpoint_info in HACKERNEWS_API_ENDPOINTS.items():
        lines.append(_generate_method(method_name, endpoint_info))
        lines.append('')

    lines.extend([
        '    async def get_api_info(self) -> HackerNewsResponse:',
        '        """Get information about the HackerNews API client.',
        '        ',
        '        Returns:',
        '            HackerNewsResponse: Information about available API methods',
        '        """',
        '        info = {',
        f"            'total_methods': {len(HACKERNEWS_API_ENDPOINTS)},",
        "            'base_url': self.base_url,",
        "            'api_categories': [",
        "                'Items & Users (2 methods)',",
        "                'Live data: max item id, story lists, updates (8 methods)',",
        "            ]",
        '        }',
        '        return HackerNewsResponse(success=True, data=info)',
    ])

    return '\n'.join(lines)


def main() -> None:
    """Generate and save the HackerNews datasource."""
    parser = argparse.ArgumentParser(
        description='Generate the HackerNews API DataSource'
    )
    parser.add_argument(
        '--out',
        default='hackernews/hackernews_data_source.py',
        help='Output path for the generated datasource'
    )
    parser.add_argument(
        '--print',
        dest='do_print',
        action='store_true',
        help='Print generated code to stdout'
    )

    args = parser.parse_args()

    print('🚀 Generating HackerNews API DataSource...')
    print(f'📊 Total endpoints: {len(HACKERNEWS_API_ENDPOINTS)}')

    code = generate_hackernews_datasource()

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code, encoding='utf-8')

    print('✅ Generated HackerNewsDataSource successfully!')
    print(f'📁 Saved to: {output_path}')
    print(f'\n📋 Summary: {len(HACKERNEWS_API_ENDPOINTS)} API methods, all explicitly typed, no auth required.')

    if args.do_print:
        print('\n' + '=' * 80)
        print('GENERATED CODE:')
        print('=' * 80 + '\n')
        print(code)


if __name__ == '__main__':
    main()
