# ruff: noqa
"""
GitHub Activity Notifier
Fetches recent stargazers and forkers (last 24 hours) and sends notifications to Slack.
"""

from datetime import datetime, timedelta, timezone
import os
import requests  # type: ignore
from typing import Any, Dict, List


class GitHubActivityNotifier:
    """Fetches and notifies about GitHub stargazers and forkers."""
    
    def __init__(self, github_token: str, slack_webhook: str, owner: str, repo: str) -> None:
        self.github_token = github_token
        self.slack_webhook = slack_webhook
        self.owner = owner
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    
    def get_recent_stargazers(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Fetch stargazers from the last N hours."""
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/stargazers"
        headers = self.headers.copy()
        headers["Accept"] = "application/vnd.github.star+json"
        
        params = {"per_page": 100, "page": 1}
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent_stargazers = []
        
        try:
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                stargazers_page = response.json()

                if not stargazers_page:
                    break

                all_on_page_were_recent = True
                for sg in stargazers_page:
                    starred_at = datetime.fromisoformat(sg['starred_at'].replace('Z', '+00:00'))
                    if starred_at >= cutoff_time:
                        recent_stargazers.append(sg)
                    else:
                        # Stargazers are sorted by most recent, so we can stop.
                        all_on_page_were_recent = False
                        break
                
                if not all_on_page_were_recent or len(stargazers_page) < params['per_page']:
                    break
                
                params['page'] += 1
            
            return recent_stargazers
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching stargazers: {e}")
            return []
    
    def get_recent_forks(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Fetch forks from the last N hours."""
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/forks"
        params = {"per_page": 100, "page": 1, "sort": "newest"}
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent_forks = []
        
        try:
            while True:
                response = requests.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                forks_page = response.json()

                if not forks_page:
                    break

                all_on_page_were_recent = True
                for fork in forks_page:
                    created_at = datetime.fromisoformat(fork['created_at'].replace('Z', '+00:00'))
                    if created_at >= cutoff_time:
                        recent_forks.append(fork)
                    else:
                        all_on_page_were_recent = False
                        break
                
                if not all_on_page_were_recent or len(forks_page) < params['per_page']:
                    break
                
                params['page'] += 1
            
            return recent_forks
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching forks: {e}")
            return []
    
    def get_user_details(self, username: str) -> Dict[str, Any]:
        """Fetch detailed user information."""
        url = f"{self.base_url}/users/{username}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching user {username}: {e}")
            return {}
    
    def format_slack_message(self, user_details: Dict[str, Any], activity_type: str, 
                            timestamp: str) -> Dict[str, Any]:
        """Format user data into a Slack message block."""
        username = user_details.get('login', 'Unknown')
        name = user_details.get('name', '')
        bio = user_details.get('bio', '')
        location = user_details.get('location', '')
        company = user_details.get('company', '')
        email = user_details.get('email', '')
        twitter = user_details.get('twitter_username', '')
        blog = user_details.get('blog', '')
        profile_url = user_details.get('html_url', f"https://github.com/{username}")
        followers = user_details.get('followers', 0)
        public_repos = user_details.get('public_repos', 0)
        
        # Build emoji and action text
        emoji = "⭐" if activity_type == "starred" else "🍴"
        action = "Starred" if activity_type == "starred" else "Forked"
        
        # Build message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {action} by {name or username}",
                    "emoji": True
                }
            }
        ]
        
        # Main profile section
        fields = [
            {
                "type": "mrkdwn",
                "text": f"*GitHub:*\n<{profile_url}|@{username}>"
            }
        ]
        
        if name:
            fields.append({"type": "mrkdwn", "text": f"*Name:*\n{name}"})
        
        if company:
            fields.append({"type": "mrkdwn", "text": f"*Company:*\n{company}"})
        
        if location:
            fields.append({"type": "mrkdwn", "text": f"*Location:*\n{location}"})
        
        fields.extend([
            {"type": "mrkdwn", "text": f"*Followers:*\n{followers}"},
            {"type": "mrkdwn", "text": f"*Repos:*\n{public_repos}"}
        ])
        
        blocks.append({
            "type": "section",
            "fields": fields
        })
        
        # Bio section
        if bio:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Bio:*\n{bio[:300]}"
                }
            })
        
        # Links section
        links = []
        if email:
            links.append(f"📧 {email}")
        if twitter:
            links.append(f"🐦 <https://twitter.com/{twitter}|@{twitter}>")
        if blog:
            blog_url = blog if blog.startswith('http') else f"https://{blog}"
            links.append(f"🌐 <{blog_url}|Website>")
        
        if links:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Links:*\n" + " • ".join(links)
                }
            })
        
        # Timestamp
        formatted_timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S UTC')
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏰ {action} at: {formatted_timestamp}"
                }
            ]
        })
        
        blocks.append({"type": "divider"})
        
        return {"blocks": blocks}
    
    def send_slack_message(self, message: Dict[str, Any]) -> None:
        """Send a message to Slack."""
        try:
            response = requests.post(
                self.slack_webhook,
                json=message,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            print("✓ Sent to Slack")
        except requests.exceptions.RequestException as e:
            print(f"✗ Error sending to Slack: {e}")
    
    def send_summary(self, stargazers_count: int, forks_count: int) -> None:
        """Send summary message."""
        if stargazers_count == 0 and forks_count == 0:
            text = "📊 *Daily Activity Report*\n\nNo new stargazers or forks in the last 24 hours."
        else:
            text = f"📊 *Daily Activity Report*\n\n⭐ Stargazers: {stargazers_count}\n🍴 Forks: {forks_count}"
        
        message = {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text
                    }
                }
            ]
        }
        self.send_slack_message(message)
    
    def run(self) -> None:
        """Main execution."""
        print("=" * 70)
        print(f"GitHub Activity Notifier - {self.owner}/{self.repo}")
        print("=" * 70)
        
        # Fetch recent activity
        print("\nFetching stargazers from last 24 hours...")
        stargazers = self.get_recent_stargazers(hours=24)
        print(f"Found {len(stargazers)} recent stargazers")
        
        print("\nFetching forks from last 24 hours...")
        forks = self.get_recent_forks(hours=24)
        print(f"Found {len(forks)} recent forks")
        
        # Process stargazers
        for sg in reversed(stargazers):  # Oldest first
            user = sg.get('user', {})
            username = user.get('login')
            starred_at = sg.get('starred_at', '')
            
            if username:
                print(f"\nProcessing stargazer: {username}")
                user_details = self.get_user_details(username)
                
                if user_details:
                    message = self.format_slack_message(user_details, "starred", starred_at)
                    self.send_slack_message(message)
        
        # Process forks
        for fork in reversed(forks):  # Oldest first
            owner = fork.get('owner', {})
            username = owner.get('login')
            created_at = fork.get('created_at', '')
            
            if username:
                print(f"\nProcessing fork by: {username}")
                user_details = self.get_user_details(username)
                
                if user_details:
                    message = self.format_slack_message(user_details, "forked", created_at)
                    self.send_slack_message(message)
        
        # Send summary
        print("\nSending summary...")
        self.send_summary(len(stargazers), len(forks))
        
        print("\n" + "=" * 70)
        print("✓ Completed!")
        print("=" * 70)


def main() -> None:
    """Main function."""
    github_token = os.getenv('GITHUB_TOKEN')
    slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
    owner = os.getenv('REPO_OWNER', 'pipeshub-ai')
    repo = os.getenv('REPO_NAME', 'pipeshub-ai')
    
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable is required")
    
    if not slack_webhook:
        raise ValueError("SLACK_WEBHOOK_URL environment variable is required")
    
    notifier = GitHubActivityNotifier(github_token, slack_webhook, owner, repo)
    notifier.run()


if __name__ == "__main__":
    main()