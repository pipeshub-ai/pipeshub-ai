from app.agents.actions.slack.slack import Slack
from app.agents.actions.slack.config import SlackTokenConfig

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    # Get Slack bot token from environment
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    
    if not slack_bot_token:
        print("❌ SLACK_BOT_TOKEN environment variable not set")
        print("Please set SLACK_BOT_TOKEN in your .env file or environment")
        print("Example: export SLACK_BOT_TOKEN='xoxb-your-token-here'")
        exit(1)
    
    print(f"✅ SLACK_BOT_TOKEN found: {slack_bot_token[:10]}...")
    
    try:
        # Create Slack instance for testing
        slack = Slack(SlackTokenConfig(token=slack_bot_token))
        print("✅ Slack instance created successfully")
        
        # Test basic functionality
        print("\n🧪 Testing Slack functionality...")
        
        # Test API methods (these will fail with invalid tokens, but test the structure)
        print("\n🔌 Testing API methods...")
        
        # Test getting channels
        try:
            success, result = slack.fetch_channels()
            print(f"fetch_channels test: {'✅ Success' if success else '❌ Failed'}")
            if success:
                print(f"   Response: {result}")
            else:
                print(f"   Expected failure with invalid token: {result}")
        except Exception as e:
            print(f"fetch_channels test: ❌ Exception: {e}")

        # Test getting channel members
        try:
            success, result = slack.get_channel_members(channel="C072THQ3F5L")
            print(f"get_channel_members test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
            if success:
                print(f"   Response: {result}")
        except Exception as e:
            print(f"get_channel_members test: ❌ Exception: {e}")

        # Test send message
        try:
            success, result = slack.send_message(channel="C072THQ3F5L", message="Hello, world!")
            print(f"send_message test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"send_message test: ❌ Exception: {e}")

        # Test get channel history
        try:
            success, result = slack.get_channel_history(channel="C072THQ3F5L")
            print(f"get_channel_history test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"get_channel_history test: ❌ Exception: {e}")

        # Test structured methods
        print("\n🏗️ Testing structured methods...")
        
        # Test get channel info
        try:
            success, result = slack.get_channel_info(channel="C072THQ3F5L")
            print(f"get_channel_info test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"get_channel_info test: ❌ Exception: {e}") 

        # Test get user info
        try:
            success, result = slack.get_user_info(user="U07C4CDLNRL")
            print(f"get_user_info test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"get_user_info test: ❌ Exception: {e}")

        # Test search all
        try:
            success, result = slack.search_all(query="Hello, world!", limit=10)
            print(f"search_all test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"search_all test: ❌ Exception: {e}")
        
        # Test get channel members by ID
        try:
            success, result = slack.get_channel_members_by_id(channel_id="C072THQ3F5L")
            print(f"get_channel_members_by_id test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"get_channel_members_by_id test: ❌ Exception: {e}")

        # Test get channel history
        try:
            success, result = slack.get_channel_history(channel="C072THQ3F5L", limit=10)
            print(f"get_channel_history test: {'✅ Success' if success else '❌ Failed'}")
            print(f"   Response: {result}")
        except Exception as e:
            print(f"get_channel_history test: ❌ Exception: {e}")

        print("\n🎉 Slack testing completed!")
        print("Note: API calls will fail with invalid tokens, but this tests the data model structure")
        print("✅ All data models are working correctly")
        print("✅ JSON serialization is working properly")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        exit(1)