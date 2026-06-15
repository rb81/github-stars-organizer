#!/usr/bin/env python3
"""
GitHub Stars Organizer
Fetches your starred repos, categorizes them with an LLM, and generates a browsable HTML wiki.
"""

# Suppress urllib3 warning about OpenSSL version - must be before imports
import warnings
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import os
import json
import signal
import sys
import argparse
from typing import Dict, List, Optional, Any
from pathlib import Path

import yaml
from github import Github, GithubException, Auth
from openai import OpenAI
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.table import Table

console = Console()

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    global shutdown_requested
    shutdown_requested = True
    console.print("\n[yellow]⚠ Interrupt received. Saving progress and exiting gracefully...[/yellow]")

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class Config:
    """Configuration manager."""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.data = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not os.path.exists(self.config_path):
            console.print(f"[red]Error: Configuration file '{self.config_path}' not found![/red]")
            console.print("[yellow]Please copy 'config.yaml.template' to 'config.yaml' and fill in your details.[/yellow]")
            sys.exit(1)
        
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Validate required fields
        if not config.get('github_token'):
            console.print("[red]Error: 'github_token' is required in config.yaml[/red]")
            sys.exit(1)
        
        if not config.get('llm', {}).get('base_url'):
            console.print("[red]Error: 'llm.base_url' is required in config.yaml[/red]")
            sys.exit(1)
        
        if not config.get('llm', {}).get('model'):
            console.print("[red]Error: 'llm.model' is required in config.yaml[/red]")
            sys.exit(1)
        
        return config
    
    @property
    def github_token(self) -> str:
        return self.data['github_token']
    
    @property
    def llm_base_url(self) -> str:
        return self.data['llm']['base_url']
    
    @property
    def llm_api_key(self) -> Optional[str]:
        return self.data['llm'].get('api_key')
    
    @property
    def llm_model(self) -> str:
        return self.data['llm']['model']
    
    @property
    def readme_max_chars(self) -> int:
        return self.data.get('readme_max_chars', 5000)


class Cache:
    """Manages caching of stars and categorizations."""
    
    def __init__(self, cache_dir: str = "cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        self.stars_file = self.cache_dir / "stars.json"
        self.categories_file = self.cache_dir / "categories.json"
        
        self.stars = self._load_json(self.stars_file, {})
        self.categories = self._load_json(self.categories_file, {})
    
    def _load_json(self, path: Path, default: Any) -> Any:
        """Load JSON file or return default."""
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
        return default
    
    def save_stars(self):
        """Save stars cache."""
        with open(self.stars_file, 'w') as f:
            json.dump(self.stars, f, indent=2)
    
    def save_categories(self):
        """Save categories cache."""
        with open(self.categories_file, 'w') as f:
            json.dump(self.categories, f, indent=2)
    
    def get_star(self, repo_full_name: str) -> Optional[Dict]:
        """Get cached star data."""
        return self.stars.get(repo_full_name)
    
    def set_star(self, repo_full_name: str, data: Dict):
        """Cache star data."""
        self.stars[repo_full_name] = data
    
    def get_category(self, repo_full_name: str) -> Optional[Dict]:
        """Get cached category data."""
        return self.categories.get(repo_full_name)
    
    def set_category(self, repo_full_name: str, category: str, description: str):
        """Cache category data."""
        self.categories[repo_full_name] = {
            'category': category,
            'description': description
        }
    
    def clear_categories(self):
        """Clear all cached categories."""
        self.categories = {}
        self.save_categories()
    
    def remove_star(self, repo_full_name: str):
        """Remove a star from cache."""
        if repo_full_name in self.stars:
            del self.stars[repo_full_name]
    
    def remove_category(self, repo_full_name: str):
        """Remove a category from cache."""
        if repo_full_name in self.categories:
            del self.categories[repo_full_name]


class StarsFetcher:
    """Fetches starred repositories from GitHub."""
    
    def __init__(self, github_token: str, cache: Cache, readme_max_chars: int):
        self.github = Github(auth=Auth.Token(github_token))
        self.cache = cache
        self.readme_max_chars = readme_max_chars
    
    def fetch_all(self) -> Dict[str, Dict]:
        """Fetch all starred repos with progress tracking."""
        console.print("\n[bold cyan]Step 1: Fetching starred repositories[/bold cyan]")
        
        try:
            user = self.github.get_user()
            starred = user.get_starred()
            total = starred.totalCount
        except GithubException as e:
            console.print(f"[red]GitHub API error: {e}[/red]")
            sys.exit(1)
        
        stars_data = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task(f"[cyan]Fetching stars", total=total)
            
            for repo in starred:
                if shutdown_requested:
                    break
                
                repo_full_name = repo.full_name
                
                # Check cache first
                cached = self.cache.get_star(repo_full_name)
                if cached:
                    stars_data[repo_full_name] = cached
                    progress.advance(task)
                    continue
                
                # Fetch new data
                try:
                    readme_content = ""
                    try:
                        readme = repo.get_readme()
                        readme_content = readme.decoded_content.decode('utf-8')[:self.readme_max_chars]
                    except GithubException:
                        pass
                    
                    data = {
                        'name': repo.name,
                        'full_name': repo.full_name,
                        'url': repo.html_url,
                        'description': repo.description or '',
                        'language': repo.language or '',
                        'stars': repo.stargazers_count,
                        'archived': repo.archived,
                        'readme': readme_content,
                        'owner': repo.owner.login
                    }
                    
                    stars_data[repo_full_name] = data
                    self.cache.set_star(repo_full_name, data)
                    self.cache.save_stars()
                    
                except GithubException as e:
                    console.print(f"[yellow]Warning: Could not fetch {repo_full_name}: {e}[/yellow]")
                
                progress.advance(task)
        
        console.print(f"[green]✓ Fetched {len(stars_data)} repositories[/green]")
        return stars_data
    
    def update_stars(self) -> Dict[str, Dict]:
        """Update stars incrementally - fetch new, remove unstarred, keep existing."""
        console.print("\n[bold cyan]Step 1: Checking for updates to starred repositories[/bold cyan]")
        
        try:
            user = self.github.get_user()
            starred = user.get_starred()
            total = starred.totalCount
        except GithubException as e:
            console.print(f"[red]GitHub API error: {e}[/red]")
            sys.exit(1)
        
        # Get current starred repo names
        current_starred = set()
        for repo in starred:
            current_starred.add(repo.full_name)
        
        # Find repos to add/remove
        cached_repos = set(self.cache.stars.keys())
        new_repos = current_starred - cached_repos
        removed_repos = cached_repos - current_starred
        
        console.print(f"[cyan]Current stars: {len(current_starred)}[/cyan]")
        console.print(f"[cyan]Cached repos: {len(cached_repos)}[/cyan]")
        console.print(f"[green]New repos: {len(new_repos)}[/green]")
        console.print(f"[yellow]Removed repos: {len(removed_repos)}[/yellow]")
        
        # Remove unstarred repos from cache
        for repo_full_name in removed_repos:
            self.cache.remove_star(repo_full_name)
            self.cache.remove_category(repo_full_name)
            console.print(f"[yellow]Removed: {repo_full_name}[/yellow]")
        
        if removed_repos:
            self.cache.save_stars()
            self.cache.save_categories()
        
        # Fetch new repos
        if new_repos:
            console.print(f"\n[bold]Fetching {len(new_repos)} new repositories...[/bold]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Fetching new repos", total=len(new_repos))
                
                for repo_full_name in new_repos:
                    if shutdown_requested:
                        break
                    
                    try:
                        repo = self.github.get_repo(repo_full_name)
                        
                        readme_content = ""
                        try:
                            readme = repo.get_readme()
                            readme_content = readme.decoded_content.decode('utf-8')[:self.readme_max_chars]
                        except GithubException:
                            pass
                        
                        data = {
                            'name': repo.name,
                            'full_name': repo.full_name,
                            'url': repo.html_url,
                            'description': repo.description or '',
                            'language': repo.language or '',
                            'stars': repo.stargazers_count,
                            'archived': repo.archived,
                            'readme': readme_content,
                            'owner': repo.owner.login
                        }
                        
                        self.cache.set_star(repo_full_name, data)
                        self.cache.save_stars()
                        
                    except GithubException as e:
                        console.print(f"[yellow]Warning: Could not fetch {repo_full_name}: {e}[/yellow]")
                    
                    progress.advance(task)
        else:
            console.print("[green]✓ No new repositories to fetch[/green]")
        
        console.print(f"[green]✓ Updated repository cache[/green]")
        return self.cache.stars


class LLMCategorizer:
    """Categorizes repos using an LLM."""
    
    def __init__(self, base_url: str, api_key: Optional[str], model: str, cache: Cache):
        self.client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
        self.model = model
        self.cache = cache
    
    def categorize_all(self, stars_data: Dict[str, Dict]) -> Dict[str, Dict]:
        """Categorize all repos with progress tracking."""
        console.print("\n[bold cyan]Step 2: Categorizing repositories with LLM[/bold cyan]")
        
        # Find repos that need categorization
        to_categorize = []
        categorized = {}
        
        for repo_full_name, data in stars_data.items():
            cached_cat = self.cache.get_category(repo_full_name)
            if cached_cat:
                categorized[repo_full_name] = cached_cat
            else:
                to_categorize.append((repo_full_name, data))
        
        if not to_categorize:
            console.print("[green]✓ All repositories already categorized[/green]")
            return categorized
        
        # Get existing categories
        existing_categories = set(cat['category'] for cat in categorized.values())
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Categorizing repos", total=len(to_categorize))
            
            for repo_full_name, data in to_categorize:
                if shutdown_requested:
                    break
                
                category, description = self._categorize_repo(data, existing_categories)
                
                categorized[repo_full_name] = {
                    'category': category,
                    'description': description
                }
                
                self.cache.set_category(repo_full_name, category, description)
                self.cache.save_categories()
                
                existing_categories.add(category)
                progress.advance(task)
        
        console.print(f"[green]✓ Categorized {len(categorized)} repositories[/green]")
        return categorized
    
    def _categorize_repo(self, repo: Dict, existing_categories: set) -> tuple[str, str]:
        """Categorize a single repo using the LLM."""
        existing_cats_str = ", ".join(sorted(existing_categories)) if existing_categories else "None yet"
        
        prompt = f"""Analyze this GitHub repository and provide:
1. A category name (prefer existing categories if appropriate)
2. A brief 2-3 sentence description

Repository: {repo['name']}
Description: {repo['description']}
Language: {repo['language']}
README excerpt: {repo['readme'][:1000]}

Existing categories: {existing_cats_str}

IMPORTANT: 
- If this repo fits an existing category, use that exact category name.
- Only create a new category if the repo doesn't fit any existing ones.
- Use clear, descriptive category names like "Web Development", "AI/ML Tools", "DevOps", etc.

Respond in this exact format:
CATEGORY: <category name>
DESCRIPTION: <2-3 sentence description>"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that categorizes GitHub repositories."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            # Check if response is valid
            if not response or not response.choices or len(response.choices) == 0:
                console.print(f"[yellow]Warning: Empty LLM response for {repo['name']}[/yellow]")
                return "Uncategorized", repo['description'] or "No description available."
            
            content = response.choices[0].message.content
            
            # Check if content is None or empty
            if not content:
                console.print(f"[yellow]Warning: LLM returned no content for {repo['name']}[/yellow]")
                return "Uncategorized", repo['description'] or "No description available."
            
            content = content.strip()
            
            # Parse response
            category = "Uncategorized"
            description = repo['description'] or "No description available."
            
            for line in content.split('\n'):
                if line.startswith('CATEGORY:'):
                    category = line.replace('CATEGORY:', '').strip()
                elif line.startswith('DESCRIPTION:'):
                    description = line.replace('DESCRIPTION:', '').strip()
            
            return category, description
            
        except Exception as e:
            console.print(f"[yellow]Warning: LLM error for {repo['name']}: {str(e)}[/yellow]")
            return "Uncategorized", repo['description'] or "No description available."


class HTMLGenerator:
    """Generates the browsable HTML wiki."""
    
    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def generate(self, stars_data: Dict[str, Dict], categorized: Dict[str, Dict]):
        """Generate HTML files."""
        console.print("\n[bold cyan]Step 3: Generating HTML wiki[/bold cyan]")
        
        # Separate archived/deleted repos
        active_repos = {}
        archived_repos = {}
        
        for repo_full_name, data in stars_data.items():
            if data.get('archived', False):
                archived_repos[repo_full_name] = data
            else:
                active_repos[repo_full_name] = data
        
        # Generate main index
        self._generate_index(active_repos, categorized)
        
        # Generate archive page
        if archived_repos:
            self._generate_archive(archived_repos, categorized)
        
        console.print(f"[green]✓ Generated HTML files in {self.output_dir}/[/green]")
        console.print(f"[green]  - index.html ({len(active_repos)} active repos)[/green]")
        if archived_repos:
            console.print(f"[green]  - archive.html ({len(archived_repos)} archived repos)[/green]")
    
    def _generate_index(self, repos: Dict[str, Dict], categorized: Dict[str, Dict]):
        """Generate main index.html."""
        # Organize by category
        by_category = {}
        for repo_full_name, data in repos.items():
            cat_data = categorized.get(repo_full_name, {'category': 'Uncategorized', 'description': ''})
            category = cat_data['category']
            
            if category not in by_category:
                by_category[category] = []
            
            by_category[category].append({
                **data,
                'llm_description': cat_data['description']
            })
        
        # Sort categories and repos
        sorted_categories = sorted(by_category.keys())
        for category in by_category:
            by_category[category].sort(key=lambda x: x['name'].lower())
        
        html = self._get_html_template(sorted_categories, by_category, "GitHub Stars Wiki")
        
        with open(self.output_dir / "index.html", 'w') as f:
            f.write(html)
    
    def _generate_archive(self, repos: Dict[str, Dict], categorized: Dict[str, Dict]):
        """Generate archive.html for archived/deleted repos."""
        repos_list = []
        for repo_full_name, data in repos.items():
            cat_data = categorized.get(repo_full_name, {'category': 'Uncategorized', 'description': ''})
            repos_list.append({
                **data,
                'category': cat_data['category'],
                'llm_description': cat_data['description']
            })
        
        repos_list.sort(key=lambda x: x['name'].lower())
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Archived Repositories</title>
    <style>
        {self._get_css()}
        .archive-notice {{
            background: #fff3cd;
            border: 1px solid #ffc107;
            padding: 1rem;
            border-radius: 4px;
            margin-bottom: 2rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🗄️ Archived Repositories</h1>
        <div class="archive-notice">
            <strong>Note:</strong> These repositories are archived, deleted, or no longer maintained.
        </div>
        <div class="repo-list">
"""
        
        for repo in repos_list:
            html += self._get_repo_card_html(repo)
        
        html += """
        </div>
    </div>
</body>
</html>"""
        
        with open(self.output_dir / "archive.html", 'w') as f:
            f.write(html)
    
    def _get_html_template(self, categories: List[str], by_category: Dict, title: str) -> str:
        """Get the main HTML template."""
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        {self._get_css()}
    </style>
</head>
<body>
    <div class="sidebar">
        <h2>📚 Categories</h2>
        <div class="category-list">
"""
        
        for category in categories:
            count = len(by_category[category])
            html += f'            <a href="#" class="category-item" data-category="{category}">{category} <span class="count">({count})</span></a>\n'
        
        html += """        </div>
    </div>
    <div class="main-content">
        <h1>⭐ GitHub Stars Wiki</h1>
        <div id="repo-container">
"""
        
        for category in categories:
            html += f'            <div class="category-section" data-category="{category}">\n'
            html += f'                <h2>{category}</h2>\n'
            html += '                <div class="repo-list">\n'
            
            for repo in by_category[category]:
                html += self._get_repo_card_html(repo)
            
            html += '                </div>\n'
            html += '            </div>\n'
        
        html += """        </div>
    </div>
    <script>
        {script}
    </script>
</body>
</html>""".replace('{script}', self._get_javascript())
        
        return html
    
    def _get_repo_card_html(self, repo: Dict) -> str:
        """Generate HTML for a single repo card."""
        language_badge = f'<span class="badge language">{repo["language"]}</span>' if repo['language'] else ''
        stars_badge = f'<span class="badge stars">⭐ {repo["stars"]}</span>' if repo.get('stars') else ''
        
        return f"""                    <div class="repo-card">
                        <h3><a href="{repo['url']}" target="_blank">{repo['name']}</a></h3>
                        <div class="repo-meta">
                            {language_badge}
                            {stars_badge}
                        </div>
                        <p class="repo-description">{repo.get('llm_description', repo.get('description', ''))}</p>
                    </div>
"""
    
    def _get_css(self) -> str:
        """Get CSS styles."""
        return """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }
        
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            width: 280px;
            height: 100vh;
            background: #2c3e50;
            color: white;
            padding: 2rem 1rem;
            overflow-y: auto;
        }
        
        .sidebar h2 {
            margin-bottom: 1rem;
            font-size: 1.5rem;
        }
        
        .category-list {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        
        .category-item {
            padding: 0.75rem 1rem;
            background: #34495e;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            transition: background 0.2s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .category-item:hover, .category-item.active {
            background: #3498db;
        }
        
        .count {
            font-size: 0.85rem;
            opacity: 0.8;
        }
        
        .main-content {
            margin-left: 280px;
            padding: 2rem;
            max-width: 1200px;
        }
        
        h1 {
            margin-bottom: 2rem;
            color: #2c3e50;
        }
        
        .category-section {
            margin-bottom: 3rem;
        }
        
        .category-section.hidden {
            display: none;
        }
        
        .category-section h2 {
            margin-bottom: 1.5rem;
            color: #2c3e50;
            padding-bottom: 0.5rem;
            border-bottom: 3px solid #3498db;
        }
        
        .repo-list {
            display: grid;
            gap: 1.5rem;
        }
        
        .repo-card {
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .repo-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }
        
        .repo-card h3 {
            margin-bottom: 0.5rem;
        }
        
        .repo-card h3 a {
            color: #3498db;
            text-decoration: none;
        }
        
        .repo-card h3 a:hover {
            text-decoration: underline;
        }
        
        .repo-meta {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
            flex-wrap: wrap;
        }
        
        .badge {
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.85rem;
            font-weight: 500;
        }
        
        .badge.language {
            background: #e8f4f8;
            color: #2980b9;
        }
        
        .badge.stars {
            background: #fff3cd;
            color: #856404;
        }
        
        .repo-description {
            color: #666;
            line-height: 1.5;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        @media (max-width: 768px) {
            .sidebar {
                position: static;
                width: 100%;
                height: auto;
            }
            
            .main-content {
                margin-left: 0;
            }
        }
        """
    
    def _get_javascript(self) -> str:
        """Get JavaScript for interactivity."""
        return """
        // Category filtering
        const categoryItems = document.querySelectorAll('.category-item');
        const categorySections = document.querySelectorAll('.category-section');
        
        categoryItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const category = item.dataset.category;
                
                // Update active state
                categoryItems.forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                
                // Show only selected category
                categorySections.forEach(section => {
                    if (section.dataset.category === category) {
                        section.classList.remove('hidden');
                    } else {
                        section.classList.add('hidden');
                    }
                });
            });
        });
        
        // Activate first category by default
        if (categoryItems.length > 0) {
            categoryItems[0].click();
        }
        """


def main():
    """Main entry point."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="GitHub Stars Organizer - Fetch, categorize, and browse your starred repos"
    )
    parser.add_argument(
        '--recategorize',
        action='store_true',
        help='Clear all categories and recategorize from cached stars (does not re-fetch from GitHub)'
    )
    parser.add_argument(
        '--update',
        action='store_true',
        help='Check for new/removed stars and update incrementally (preserves existing categorizations)'
    )
    args = parser.parse_args()
    
    console.print(Panel.fit(
        "[bold cyan]GitHub Stars Organizer[/bold cyan]\n"
        "Fetch, categorize, and browse your starred repos",
        border_style="cyan"
    ))
    
    # Load configuration
    config = Config()
    
    # Initialize cache
    cache = Cache()
    
    # Determine mode
    if args.recategorize:
        console.print("\n[bold yellow]MODE: Recategorization[/bold yellow]")
        console.print("[dim]Using cached star data and clearing all categories[/dim]\n")
        
        # Check if we have cached stars
        if not cache.stars:
            console.print("[red]Error: No cached star data found![/red]")
            console.print("[yellow]Run without --recategorize first to fetch stars from GitHub.[/yellow]")
            sys.exit(1)
        
        # Use cached stars
        stars_data = cache.stars
        console.print(f"[green]✓ Loaded {len(stars_data)} repositories from cache[/green]")
        
        # Clear categories
        console.print("[yellow]Clearing all categories...[/yellow]")
        cache.clear_categories()
        console.print("[green]✓ Categories cleared[/green]")
        
    elif args.update:
        console.print("\n[bold yellow]MODE: Incremental Update[/bold yellow]")
        console.print("[dim]Checking for new/removed stars and updating incrementally[/dim]\n")
        
        # Update stars incrementally
        fetcher = StarsFetcher(config.github_token, cache, config.readme_max_chars)
        stars_data = fetcher.update_stars()
        
    else:
        console.print("\n[bold yellow]MODE: Full Run[/bold yellow]")
        console.print("[dim]Fetching all stars, categorizing uncategorized repos[/dim]\n")
        
        # Fetch all stars (uses cache for existing repos)
        fetcher = StarsFetcher(config.github_token, cache, config.readme_max_chars)
        stars_data = fetcher.fetch_all()
    
    if shutdown_requested:
        console.print("[yellow]Exiting after saving progress.[/yellow]")
        return
    
    # Step 2: Categorize with LLM
    categorizer = LLMCategorizer(
        config.llm_base_url,
        config.llm_api_key,
        config.llm_model,
        cache
    )
    categorized = categorizer.categorize_all(stars_data)
    
    if shutdown_requested:
        console.print("[yellow]Exiting after saving progress.[/yellow]")
        return
    
    # Step 3: Generate HTML
    generator = HTMLGenerator()
    generator.generate(stars_data, categorized)
    
    # Display summary
    console.print("\n" + "="*60)
    console.print("[bold green]✓ Complete![/bold green]\n")
    
    # Create summary table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    
    table.add_row("Total Repositories", str(len(stars_data)))
    table.add_row("Categorized", str(len(categorized)))
    table.add_row("Categories", str(len(set(c['category'] for c in categorized.values()))))
    
    archived_count = sum(1 for d in stars_data.values() if d.get('archived', False))
    if archived_count > 0:
        table.add_row("Archived", str(archived_count))
    
    console.print(table)
    
    # Print file paths
    console.print("\n[bold]Generated files:[/bold]")
    index_path = Path("output/index.html").resolve()
    console.print(f"  📄 [cyan]{index_path}[/cyan]")
    
    if archived_count > 0:
        archive_path = Path("output/archive.html").resolve()
        console.print(f"  📄 [cyan]{archive_path}[/cyan]")
    
    console.print(f"\n[bold green]Open the file in your browser to browse your stars![/bold green]")
    console.print(f"[dim]Tip: Run 'open {index_path}' (macOS) or 'xdg-open {index_path}' (Linux)[/dim]")


if __name__ == "__main__":
    main()
