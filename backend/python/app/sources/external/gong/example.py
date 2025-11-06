# ruff: noqa
import asyncio
import os

from app.sources.client.gong.gong import GongClient, GongApiKeyConfig
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.gong.gong import GongDataSource


def main():
    """Example usage of Gong client and data source"""
    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")
    
    if not access_key or not access_key_secret:
        raise Exception("GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET environment variables are required")

    # Create Gong client using configuration
    gong_client: GongClient = GongClient.build_with_config(
        GongApiKeyConfig(
            access_key=access_key,
            access_key_secret=access_key_secret,
        ),
    )
    print(f"Created Gong client: {gong_client}")

    # Create Gong data source
    gong_data_source = GongDataSource(gong_client)
    print(f"Created Gong data source: {gong_data_source}")

    # Test various API endpoints
    asyncio.run(test_gong_apis(gong_data_source))


async def test_gong_apis(gong_data_source: GongDataSource):
    """Test various Gong API endpoints"""
    
    print("\n=== Testing Gong API Endpoints ===")
    
    try:
        # Test 1: Get workspaces
        print("\n1. Getting workspaces...")
        workspaces_response: HTTPResponse = await gong_data_source.get_workspaces()
        print(f"Workspaces Status: {workspaces_response.status}")
        print(f"Workspaces Headers: {dict(workspaces_response.headers)}")
        if workspaces_response.status == 200:
            workspaces_data = workspaces_response.json()
            print(f"Workspaces Data: {workspaces_data}")
        else:
            print(f"Workspaces Error: {workspaces_response.text}")

    except Exception as e:
        print(f"Error getting workspaces: {e}")

    try:
        # Test 2: Get users
        print("\n2. Getting users...")
        users_response: HTTPResponse = await gong_data_source.get_users(limit=10)
        print(f"Users Status: {users_response.status}")
        if users_response.status == 200:
            users_data = users_response.json()
            print(f"Users Count: {len(users_data.get('users', []))}")
            print(f"Users Sample: {users_data}")
        else:
            print(f"Users Error: {users_response.text}")

    except Exception as e:
        print(f"Error getting users: {e}")

    try:
        # Test 3: Get calls (last 30 days)
        print("\n3. Getting recent calls...")
        from datetime import datetime, timedelta, timezone
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=30)
        
        from_date = start_date.isoformat().replace('+00:00', 'Z')
        to_date = end_date.isoformat().replace('+00:00', 'Z')
        
        calls_response: HTTPResponse = await gong_data_source.get_calls(
            from_date_time=from_date,
            to_date_time=to_date,
            limit=5
        )
        print(f"Calls Status: {calls_response.status}")
        if calls_response.status == 200:
            calls_data = calls_response.json()
            print(f"Calls Count: {len(calls_data.get('calls', []))}")
            calls = calls_data.get('calls', [])
            if calls:
                print(f"First Call Sample: {calls[0]}")
                
                # Test 4: Get call details for the first call
                call_id = calls[0].get('id')
                if call_id:
                    print(f"\n4. Getting call details for call ID: {call_id}")
                    call_details_response: HTTPResponse = await gong_data_source.get_call_details(call_id)
                    print(f"Call Details Status: {call_details_response.status}")
                    if call_details_response.status == 200:
                        call_details = call_details_response.json()
                        print(f"Call Details: {call_details}")
                    else:
                        print(f"Call Details Error: {call_details_response.text}")
                        
                    # Test 5: Get call transcript
                    print(f"\n5. Getting call transcript for call ID: {call_id}")
                    transcript_response: HTTPResponse = await gong_data_source.get_call_transcript(call_id)
                    print(f"Transcript Status: {transcript_response.status}")
                    if transcript_response.status == 200:
                        transcript_data = transcript_response.json()
                        entries = transcript_data.get('entries', [])
                        print(f"Transcript Entries Count: {len(entries)}")
                        if entries:
                            print(f"First Transcript Entry: {entries[0]}")
                    else:
                        print(f"Transcript Error: {transcript_response.text}")
            else:
                print("No calls found in the specified date range")
        else:
            print(f"Calls Error: {calls_response.text}")

    except Exception as e:
        print(f"Error getting calls: {e}")

    try:
        # Test 6: Get deals
        print("\n6. Getting deals...")
        deals_response: HTTPResponse = await gong_data_source.get_deals(limit=5)
        print(f"Deals Status: {deals_response.status}")
        if deals_response.status == 200:
            deals_data = deals_response.json()
            print(f"Deals Count: {len(deals_data.get('deals', []))}")
            print(f"Deals Sample: {deals_data}")
        else:
            print(f"Deals Error: {deals_response.text}")

    except Exception as e:
        print(f"Error getting deals: {e}")

    try:
        # Test 7: Get meetings
        print("\n7. Getting meetings...")
        from datetime import datetime, timedelta, timezone
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)  # Last 7 days
        
        from_date = start_date.isoformat().replace('+00:00', 'Z')
        to_date = end_date.isoformat().replace('+00:00', 'Z')
        
        meetings_response: HTTPResponse = await gong_data_source.get_meetings(
            from_date_time=from_date,
            to_date_time=to_date,
            limit=5
        )
        print(f"Meetings Status: {meetings_response.status}")
        if meetings_response.status == 200:
            meetings_data = meetings_response.json()
            print(f"Meetings Count: {len(meetings_data.get('meetings', []))}")
            print(f"Meetings Sample: {meetings_data}")
        else:
            print(f"Meetings Error: {meetings_response.text}")

    except Exception as e:
        print(f"Error getting meetings: {e}")

    try:
        # Test 8: Get CRM objects
        print("\n8. Getting CRM objects...")
        crm_response: HTTPResponse = await gong_data_source.get_crm_objects(limit=5)
        print(f"CRM Objects Status: {crm_response.status}")
        if crm_response.status == 200:
            crm_data = crm_response.json()
            print(f"CRM Objects Sample: {crm_data}")
        else:
            print(f"CRM Objects Error: {crm_response.text}")

    except Exception as e:
        print(f"Error getting CRM objects: {e}")

    try:
        # Test 9: Get activity statistics
        print("\n9. Getting activity statistics...")
        from datetime import datetime, timedelta, timezone
        
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=30)
        
        from_date = start_date.isoformat().replace('+00:00', 'Z')
        to_date = end_date.isoformat().replace('+00:00', 'Z')
        
        stats_response: HTTPResponse = await gong_data_source.get_stats_activity(
            from_date_time=from_date,
            to_date_time=to_date,
            limit=5
        )
        print(f"Activity Stats Status: {stats_response.status}")
        if stats_response.status == 200:
            stats_data = stats_response.json()
            print(f"Activity Stats Sample: {stats_data}")
        else:
            print(f"Activity Stats Error: {stats_response.text}")

    except Exception as e:
        print(f"Error getting activity statistics: {e}")

    try:
        # Test 10: Get library calls
        print("\n10. Getting library calls...")
        library_response: HTTPResponse = await gong_data_source.get_library_calls(limit=5)
        print(f"Library Calls Status: {library_response.status}")
        if library_response.status == 200:
            library_data = library_response.json()
            print(f"Library Calls Sample: {library_data}")
        else:
            print(f"Library Calls Error: {library_response.text}")

    except Exception as e:
        print(f"Error getting library calls: {e}")

    print("\n=== Gong API Testing Complete ===")


if __name__ == "__main__":
    main()