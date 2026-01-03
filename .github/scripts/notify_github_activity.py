#!/usr/bin/env python3
# ruff: noqa
"""
GitHub Activity Notifier with Contact Information Extraction
Fetches stargazers and forkers based on hours parameter.
"""

from datetime import datetime, timedelta, timezone
import os
import re
import requests  # type: ignore
from typing import Any, Dict, List, Optional


class GitHubActivityNotifier:
    """Fetches and notifies about GitHub stargazers and forkers with contact data."""
    
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
    
    def get_recent_stargazers(self, hours) -> List[Dict[str, Any]]:
        """
        Fetch stargazers from the last N hours.
        
        Strategy:
        1. Fetch ALL stargazers from the repository
        2. Sort them by starred_at timestamp
        3. Filter for only those within the cutoff time window
        
        Args:
            hours: Number of hours to look back
        
        Returns:
            List of stargazers from the last N hours (sorted oldest to newest)
        """
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/stargazers"
        headers = self.headers.copy()
        headers["Accept"] = "application/vnd.github.star+json"  # Required for timestamps
        
        # Calculate cutoff time
        current_time = datetime.now(timezone.utc)
        cutoff_time = current_time - timedelta(hours=hours)
        
        print(f"\n{'='*70}")
        print(f"Fetching ALL Stargazers")
        print(f"{'='*70}")
        print(f"⏰ Looking back: {hours} hours")
        print(f"📅 Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"✂️  Cutoff time: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        all_stargazers = []
        # GitHub API max per_page is 100, not 500
        params = {"per_page": 100, "page": 1}
        
        try:
            # Fetch ALL pages until no more stargazers
            while True:
                print(f"📄 Fetching page {params['page']}...", end=" ")
                response = requests.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
                stargazers_page = response.json()
                
                if not stargazers_page:
                    print("(empty - no more stars)")
                    break
                
                print(f"({len(stargazers_page)} stars)")
                
                # Add all stars from this page
                all_stargazers.extend(stargazers_page)
                
                # Show timestamp range for this page
                if stargazers_page:
                    try:
                        first_time = datetime.fromisoformat(stargazers_page[0].get('starred_at', '').replace('Z', '+00:00'))
                        last_time = datetime.fromisoformat(stargazers_page[-1].get('starred_at', '').replace('Z', '+00:00'))
                        print(f"   Range: {first_time.strftime('%m/%d %H:%M')} → {last_time.strftime('%m/%d %H:%M')}")
                    except (ValueError, AttributeError):
                        pass
                
                # If we got fewer than requested, we've reached the end
                if len(stargazers_page) < params['per_page']:
                    print(f"   ✓ Reached last page")
                    break
                
                params['page'] += 1
            
            print(f"\n📊 Total fetched: {len(all_stargazers)} stars from {params['page']} pages")
            
            if not all_stargazers:
                print(f"\n✅ No stargazers found in repository")
                return []
            
            # Sort by starred_at timestamp (oldest first)
            print(f"🔄 Sorting {len(all_stargazers)} stars by timestamp...")
            all_stargazers.sort(
                key=lambda x: datetime.fromisoformat(x.get('starred_at', '1970-01-01T00:00:00Z').replace('Z', '+00:00'))
            )
            
            # Filter for stars within our time window
            print(f"✂️  Filtering for stars after {cutoff_time.strftime('%Y-%m-%d %H:%M:%S UTC')}...")
            recent_stargazers = []
            
            for sg in all_stargazers:
                starred_at_str = sg.get('starred_at', '')
                if not starred_at_str:
                    continue
                
                try:
                    starred_at = datetime.fromisoformat(starred_at_str.replace('Z', '+00:00'))
                    
                    if starred_at >= cutoff_time:
                        recent_stargazers.append(sg)
                except (ValueError, AttributeError):
                    continue
            
            # Show summary
            print(f"\n{'='*70}")
            if recent_stargazers:
                first_star = recent_stargazers[0]
                last_star = recent_stargazers[-1]
                first_time = datetime.fromisoformat(first_star['starred_at'].replace('Z', '+00:00'))
                last_time = datetime.fromisoformat(last_star['starred_at'].replace('Z', '+00:00'))
                
                print(f"✅ FOUND {len(recent_stargazers)} stargazers in last {hours} hours")
                print(f"{'='*70}")
                print(f"   First: @{first_star['user']['login']} at {first_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"   Last:  @{last_star['user']['login']} at {last_time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"✅ No stargazers found in last {hours} hours")
                print(f"   (Out of {len(all_stargazers)} total stars)")
                print(f"{'='*70}")
            
            return recent_stargazers
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching stargazers: {e}")
            return []

    def get_recent_forks(self, hours) -> List[Dict[str, Any]]:
        """
        Fetch forks from the last N hours.
        Cutoff time is calculated as: current_time - hours
        
        Args:
            hours: Number of hours to look back
        
        Returns:
            List of forks from the last N hours
        """
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/forks"
        params = {"per_page": 100, "page": 1, "sort": "newest"}
        
        # Calculate cutoff time based on hours parameter
        current_time = datetime.now(timezone.utc)
        cutoff_time = current_time - timedelta(hours=hours)
        
        all_forks = []
        
        print(f"\n{'='*70}")
        print(f"Fetching Forks")
        print(f"{'='*70}")
        print(f"Hours parameter: {hours}")
        print(f"Current time (UTC): {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Cutoff time (UTC): {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Fetching forks from last {hours} hours")
        
        try:
            while True:
                response = requests.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                forks_page = response.json()

                if not forks_page:
                    print(f"\nNo more forks to fetch")
                    break
                
                print(f"\nPage {params['page']}: Fetched {len(forks_page)} forks")
                
                if forks_page:
                    first_fork = forks_page[0]
                    last_fork = forks_page[-1]
                    print(f"  First: @{first_fork.get('owner', {}).get('login')} at {first_fork.get('created_at')}")
                    print(f"  Last:  @{last_fork.get('owner', {}).get('login')} at {last_fork.get('created_at')}")
                
                page_added = 0
                for fork in forks_page:
                    created_at_str = fork.get('created_at', '')
                    
                    if not created_at_str:
                        continue
                    
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    
                    if created_at >= cutoff_time:
                        all_forks.append(fork)
                        page_added += 1
                    else:
                        print(f"  Reached cutoff time ({cutoff_time.strftime('%Y-%m-%d %H:%M:%S')})")
                        print(f"  Added {page_added} from this page")
                        print(f"\n✓ Total forks found: {len(all_forks)}")
                        return all_forks
                
                print(f"  Added {page_added} from this page (Running total: {len(all_forks)})")
                
                if len(forks_page) < params['per_page']:
                    print(f"\nReached last page")
                    break
                
                params['page'] += 1
            
            print(f"\n✓ Total forks found: {len(all_forks)}")
            return all_forks
            
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
    
    def extract_social_links(self, bio: Optional[str], blog: Optional[str], 
                            twitter_username: Optional[str]) -> Dict[str, Any]:
        """Extract comprehensive social media and contact links from user profile."""
        result = {
            'linkedin_url': None,
            'twitter_url': None,
            'website': None,
            'email': None,
            'phone': None,
            'other_social': []
        }
        
        if twitter_username:
            result['twitter_url'] = f"https://twitter.com/{twitter_username}"
        
        if blog:
            blog = blog.strip()
            if 'linkedin.com' in blog.lower():
                result['linkedin_url'] = blog if blog.startswith('http') else f"https://{blog}"
            elif 'twitter.com' in blog.lower() or 'x.com' in blog.lower():
                if not result['twitter_url']:
                    result['twitter_url'] = blog if blog.startswith('http') else f"https://{blog}"
            else:
                result['website'] = blog if blog.startswith('http') else f"https://{blog}"
        
        if bio:
            # LinkedIn
            linkedin_patterns = [
                r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?',
                r'(?:https?://)?(?:www\.)?linkedin\.com/company/[\w-]+/?'
            ]
            for pattern in linkedin_patterns:
                matches = re.findall(pattern, bio, re.IGNORECASE)
                if matches and not result['linkedin_url']:
                    url = matches[0]
                    result['linkedin_url'] = url if url.startswith('http') else f"https://{url}"
                    break
            
            # Twitter/X
            twitter_patterns = [
                r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/[\w]+/?',
                r'@([\w]+)(?:\s|$)'
            ]
            for pattern in twitter_patterns:
                matches = re.findall(pattern, bio, re.IGNORECASE)
                if matches and not result['twitter_url']:
                    if pattern.startswith('@'):
                        result['twitter_url'] = f"https://twitter.com/{matches[0]}"
                    else:
                        url = matches[0]
                        result['twitter_url'] = url if url.startswith('http') else f"https://{url}"
                    break
            
            # Email
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            email_matches = re.findall(email_pattern, bio)
            if email_matches:
                result['email'] = email_matches[0]
            
            # Phone
            phone_patterns = [
                r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}',
                r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}',
            ]
            for pattern in phone_patterns:
                phone_matches = re.findall(pattern, bio)
                if phone_matches:
                    result['phone'] = phone_matches[0]
                    break
            
            # Other social media
            social_patterns = {
                'Discord': r'(?:discord\.gg/|discordapp\.com/users/)[\w-]+',
                'Telegram': r'(?:t\.me|telegram\.me)/[\w]+',
                'Medium': r'(?:https?://)?(?:www\.)?medium\.com/@?[\w-]+',
                'Dev.to': r'(?:https?://)?(?:www\.)?dev\.to/[\w-]+',
                'YouTube': r'(?:https?://)?(?:www\.)?youtube\.com/[@\w-]+',
                'Instagram': r'(?:https?://)?(?:www\.)?instagram\.com/[\w.]+',
                'Facebook': r'(?:https?://)?(?:www\.)?facebook\.com/[\w.]+',
                'Mastodon': r'@[\w]+@[\w.-]+',
            }
            
            for platform, pattern in social_patterns.items():
                matches = re.findall(pattern, bio, re.IGNORECASE)
                if matches:
                    url = matches[0]
                    if not url.startswith('http') and platform != 'Mastodon':
                        url = f"https://{url}"
                    result['other_social'].append({
                        'platform': platform,
                        'url': url
                    })
        
        return result
    
    def format_slack_message(self, user_details: Dict[str, Any], activity_type: str, 
                            timestamp: str, social_links: Dict[str, Any]) -> Dict[str, Any]:
        """Format user data into a comprehensive Slack message."""
        username = user_details.get('login', 'Unknown')
        name = user_details.get('name', '')
        bio = user_details.get('bio', '')
        location = user_details.get('location', '')
        company = user_details.get('company', '')
        email = user_details.get('email') or social_links.get('email', '')
        profile_url = user_details.get('html_url', f"https://github.com/{username}")
        followers = user_details.get('followers', 0)
        public_repos = user_details.get('public_repos', 0)
        created_at = user_details.get('created_at', '')
        
        if created_at:
            account_created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            account_age_days = (datetime.now(timezone.utc) - account_created).days
            account_age_years = account_age_days / 365.25
        else:
            account_age_years = 0
        
        emoji = "⭐" if activity_type == "starred" else "🍴"
        action = "Starred" if activity_type == "starred" else "Forked"
        
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
        
        fields = []
        fields.append({"type": "mrkdwn", "text": f"*GitHub:*\n<{profile_url}|@{username}>"})
        
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
        
        if account_age_years > 0:
            fields.append({"type": "mrkdwn", "text": f"*Account Age:*\n{account_age_years:.1f} years"})
        
        blocks.append({"type": "section", "fields": fields})
        
        if bio:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Bio:*\n{bio[:300]}"}
            })
        
        contact_items = []
        if email:
            contact_items.append(f"📧 *Email:* {email}")
        if social_links.get('phone'):
            contact_items.append(f"📱 *Phone:* {social_links['phone']}")
        if social_links.get('linkedin_url'):
            contact_items.append(f"💼 *LinkedIn:* <{social_links['linkedin_url']}|View Profile>")
        if social_links.get('twitter_url'):
            contact_items.append(f"🐦 *Twitter/X:* <{social_links['twitter_url']}|View Profile>")
        if social_links.get('website'):
            contact_items.append(f"🌐 *Website:* <{social_links['website']}|Visit>")
        
        for social in social_links.get('other_social', [])[:3]:
            contact_items.append(f"🔗 *{social['platform']}:* <{social['url']}|View>")
        
        if contact_items:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Contact & Social Profiles:*\n" + "\n".join(contact_items)}
            })
        
        formatted_timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S UTC')
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{emoji} {action} at: {formatted_timestamp}"}]
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
    
    def send_summary(self, stargazers_count: int, forks_count: int, hours: int) -> None:
        """Send summary message."""
        if stargazers_count == 0 and forks_count == 0:
            text = f"📊 *Activity Report*\n\nNo new stargazers or forks in the last {hours} hours."
        else:
            text = f"📊 *Activity Report*\n\nLast {hours} hours:\n⭐ Stargazers: {stargazers_count}\n🍴 Forks: {forks_count}"
        
        message = {
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}}
            ]
        }
        self.send_slack_message(message)
    
    def run(self, hours) -> None:
        """Main execution."""
        print("=" * 70)
        print(f"GitHub Activity Notifier - {self.owner}/{self.repo}")
        print("=" * 70)
        
        # Fetch activity based on hours parameter
        stargazers = self.get_recent_stargazers(hours)
        forks = self.get_recent_forks(hours)
        
        # Process stargazers (oldest first)
        if stargazers:
            print(f"\n{'='*70}")
            print(f"Processing {len(stargazers)} Stargazers")
            print(f"{'='*70}")
        
        for idx, sg in enumerate(reversed(stargazers), 1):
            user = sg.get('user', {})
            username = user.get('login')
            starred_at = sg.get('starred_at', '')
            
            if username:
                print(f"\n[{idx}/{len(stargazers)}] Processing: @{username}")
                user_details = self.get_user_details(username)
                
                if user_details:
                    social_links = self.extract_social_links(
                        user_details.get('bio'),
                        user_details.get('blog'),
                        user_details.get('twitter_username')
                    )
                    
                    message = self.format_slack_message(
                        user_details, "starred", starred_at, social_links
                    )
                    #self.send_slack_message(message)
        
        # Process forks
        if forks:
            print(f"\n{'='*70}")
            print(f"Processing {len(forks)} Forks")
            print(f"{'='*70}")
        
        for idx, fork in enumerate(reversed(forks), 1):
            owner = fork.get('owner', {})
            username = owner.get('login')
            created_at = fork.get('created_at', '')
            
            if username:
                print(f"\n[{idx}/{len(forks)}] Processing: @{username}")
                user_details = self.get_user_details(username)
                
                if user_details:
                    social_links = self.extract_social_links(
                        user_details.get('bio'),
                        user_details.get('blog'),
                        user_details.get('twitter_username')
                    )
                    
                    message = self.format_slack_message(
                        user_details, "forked", created_at, social_links
                    )
                    #self.send_slack_message(message)
        
        # Send summary
        print(f"\n{'='*70}")
        print("Sending Summary")
        print(f"{'='*70}")
        #self.send_summary(len(stargazers), len(forks), hours)
        
        print(f"\n{'='*70}")
        print("✓ Completed!")
        print(f"{'='*70}")


def main() -> None:
    """Main function."""
    github_token = os.getenv('PAT_GITHUB_TOKEN')
    slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
    owner = os.getenv('REPO_OWNER', 'pipeshub-ai')
    repo = os.getenv('REPO_NAME', 'pipeshub-ai')
    
    # Get hours from environment variable, default to 24
    hours = int(os.getenv('STARGAZERS_HOURS', '24'))
    
    if not github_token:
        raise ValueError("PAT_GITHUB_TOKEN environment variable is required")
    
    if not slack_webhook:
        raise ValueError("SLACK_WEBHOOK_URL environment variable is required")
    
    notifier = GitHubActivityNotifier(github_token, slack_webhook, owner, repo)
    notifier.run(hours)


if __name__ == "__main__":
    main()