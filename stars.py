import os, json, logging, re, csv
from io import StringIO
from datetime import datetime
import argparse
from github import Github
from github.GithubException import GithubException
import anthropic
from dotenv import load_dotenv
from tqdm import tqdm

# Set up argument parser
parser = argparse.ArgumentParser(description="Organize GitHub starred repos")
parser.add_argument('--debug', action='store_true', help='Enable debug logging')
parser.add_argument('--output', default='.', help='Output folder for category lists and README')
args = parser.parse_args()

# Set up logging
logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# File paths
STARRED_REPOS_FILE = 'starred_repos.json'
CATEGORIES_FILE = 'categories.json'
REPO_CATEGORY_MAPPING_FILE = 'repo_category_mapping.json'

def load_json_file(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return {}

def save_json_file(data, file_path):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved data to {file_path}")

def download_starred_repos(github_token):
    try:
        g = Github(github_token)
        user = g.get_user()
        starred_repos = user.get_starred()

        repos = {}
        total_repos = starred_repos.totalCount
        with tqdm(total=total_repos, desc="Downloading starred repos") as pbar:
            for repo in starred_repos:
                try:
                    readme_content = repo.get_readme().decoded_content.decode('utf-8')
                except GithubException:
                    readme_content = ""

                repos[repo.full_name] = {
                    'Name': repo.name,
                    'URL': repo.html_url,
                    'Description': repo.description or '',
                    'Owner': repo.owner.login,
                    'FullName': repo.full_name,
                    'README': readme_content[:5000]  # Limit to first 5000 characters to avoid very large payloads
                }
                pbar.update(1)

        save_json_file(repos, STARRED_REPOS_FILE)
        logger.info(f"Downloaded {len(repos)} starred repos")
        return repos
    except GithubException as e:
        logger.error(f"GitHub API error: {e.status} - {e.data}")
    except Exception as e:
        logger.error(f"Unexpected error in download_starred_repos: {str(e)}")
    return {}

def organize_repos_with_claude(anthropic_api_key, repos_batch, categories):
    try:
        client = anthropic.Anthropic(api_key=anthropic_api_key)

        repos_list = [f"{repo['FullName']},{repo['URL']},{repo['Description']},{repo['README'][:500]}" for repo in repos_batch.values()]
        repos_csv = "FullName,URL,Description,README\n" + "\n".join(repos_list)

        categories_str = "\n".join([f"{cat}: {desc}" for cat, desc in categories.items()])

        prompt = f"""Analyze this CSV file containing GitHub starred repositories:

        {repos_csv}

        Use ONLY the following predefined categories to classify the repositories:
        {categories_str}

        Use both the Description and the README content to determine the most appropriate category.
        If a repository doesn't fit into any of these categories, use the category "Other".
        Return your response as a CSV with two columns: 'Repository FullName' and 'Category'.
        A repository can belong to multiple categories, but only if very relevant; in that case, add multiple rows for that repository.
        Ensure your response is a valid CSV format without any additional text."""

        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=4096,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        # Extract the CSV content from the response
        csv_response = message.content[0].text

        logger.debug(f"Claude's response: {csv_response}")

        new_mappings = {}

        csv_reader = csv.reader(StringIO(csv_response.strip()), delimiter=',')
        next(csv_reader)  # Skip header
        for row in csv_reader:
            if len(row) != 2:
                logger.warning(f"Skipping invalid row: {row}")
                continue
            repo_full_name, category = row
            
            if category not in categories and category != "Other":
                logger.warning(f"Invalid category '{category}' for repo '{repo_full_name}'. Using 'Other' instead.")
                category = "Other"
            
            if category == "Other":
                logger.info(f"Repository '{repo_full_name}' categorized as 'Other'")
            
            if repo_full_name not in new_mappings:
                new_mappings[repo_full_name] = []
            new_mappings[repo_full_name].append(category)

        logger.info(f"Organized {len(new_mappings)} repos")
        return new_mappings
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in organize_repos_with_claude: {str(e)}")
    return {}

def clean_filename(name):
    return re.sub(r'[^\w\-_\. ]', '', name).strip().replace(' ', '_').lower()

def update_github_lists(github_token, categories, starred_repos, repo_category_mapping):
    try:
        g = Github(github_token)
        repo = g.get_repo(os.getenv('GITHUB_REPO'))

        # Organize repos by category
        category_repos = {cat: [] for cat in categories}
        for repo_full_name, repo_categories in repo_category_mapping.items():
            for category in repo_categories:
                if category in category_repos:
                    category_repos[category].append(repo_full_name)

        # Get existing files in the repository
        existing_files = [file.path for file in repo.get_contents("") if file.path.endswith('.md') and file.path != "README.md"]

        changes_made = False

        output_folder = os.path.expanduser(args.output)
        os.makedirs(output_folder, exist_ok=True)

        with tqdm(total=len(category_repos), desc="Updating GitHub lists") as pbar:
            for category, repos in category_repos.items():
                file_name = f"{clean_filename(category)}.md"
                local_file_path = os.path.join(output_folder, file_name)
                
                if not repos:
                    # If category is empty, delete the file if it exists in the repo
                    if file_name in existing_files:
                        file = repo.get_contents(file_name)
                        repo.delete_file(file_name, f"Remove empty category: {category}", file.sha)
                        logger.info(f"Deleted empty category file: {file_name}")
                        changes_made = True
                    pbar.update(1)
                    continue

                content = f"# {category}\n\n{categories[category]}\n\n"
                
                for repo_full_name in repos:
                    repo_data = starred_repos[repo_full_name]
                    content += f"## [{repo_data['Name']}]({repo_data['URL']})\n\n"
                    content += f"{repo_data['Description']}\n\n"
                    content += f"[![GitHub stars](https://img.shields.io/github/stars/{repo_data['FullName']}?style=social)](https://github.com/{repo_data['FullName']})\n\n"
                    content += "---\n\n"

                # Save the content locally
                with open(local_file_path, 'w') as f:
                    f.write(content)
                logger.info(f"Saved {file_name} locally to {local_file_path}")

                if file_name in existing_files:
                    file = repo.get_contents(file_name)
                    if file.decoded_content.decode() != content:
                        repo.update_file(file_name, f"Update {category} list", content, file.sha)
                        logger.info(f"Updated {file_name} in the repo")
                        changes_made = True
                    existing_files.remove(file_name)
                else:
                    repo.create_file(file_name, f"Create {category} list", content)
                    logger.info(f"Created {file_name} in the repo")
                    changes_made = True

                pbar.update(1)

        # Delete files for categories that no longer exist or are empty
        with tqdm(total=len(existing_files), desc="Deleting obsolete files") as pbar:
            for file_name in existing_files:
                file = repo.get_contents(file_name)
                repo.delete_file(file_name, f"Remove {file_name}", file.sha)
                logger.info(f"Deleted {file_name} from the repo")
                changes_made = True
                pbar.update(1)

        return changes_made

    except GithubException as e:
        logger.error(f"GitHub API error: {e.status} - {e.data}")
    except Exception as e:
        logger.error(f"Unexpected error in update_github_lists: {str(e)}")
    
    return False

def remove_unstarred_repos(starred_repos, repo_category_mapping):
    unstarred_repos = set(repo_category_mapping.keys()) - set(starred_repos.keys())
    for repo in unstarred_repos:
        del repo_category_mapping[repo]
        logger.info(f"Removed unstarred repo from categories: {repo}")
    return list(unstarred_repos)

def update_readme(github_token, starred_repos, repo_category_mapping):
    try:
        g = Github(github_token)
        repo = g.get_repo(os.getenv('GITHUB_REPO'))

        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        total_starred = len(starred_repos)
        total_categories = len(set(cat for cats in repo_category_mapping.values() for cat in cats))

        content = f"""# Starred Repositories

- **Last Updated:** {update_time}
- **Total Starred Repositories:** {total_starred}
- **Total Categories:** {total_categories}

## Quick Stats

- Most Common Category: {get_most_common_category(repo_category_mapping)}
- Recently Added: {get_recently_added(starred_repos, 5)}

For detailed lists of repositories by category, please check the individual category files in this repository.
"""

        # Save README locally
        output_folder = os.path.expanduser(args.output)
        readme_path = os.path.join(output_folder, "README.md")
        with open(readme_path, 'w') as f:
            f.write(content)
        logger.info(f"Saved README.md locally to {readme_path}")

        try:
            file = repo.get_contents("README.md")
            repo.update_file("README.md", "Update README with latest stats", content, file.sha)
            logger.info("Updated README.md in the repo")
        except GithubException:
            repo.create_file("README.md", "Create README with stats", content)
            logger.info("Created README.md in the repo")

    except GithubException as e:
        logger.error(f"GitHub API error: {e.status} - {e.data}")
    except Exception as e:
        logger.error(f"Unexpected error in update_readme: {str(e)}")

def get_most_common_category(repo_category_mapping):
    category_counts = {}
    for categories in repo_category_mapping.values():
        for category in categories:
            category_counts[category] = category_counts.get(category, 0) + 1
    return max(category_counts, key=category_counts.get) if category_counts else "N/A"

def get_recently_added(starred_repos, num):
    sorted_repos = sorted(starred_repos.values(), key=lambda x: x['FullName'], reverse=True)
    return ", ".join([f"[{repo['Name']}]({repo['URL']})" for repo in sorted_repos[:num]])

def main():
    github_token = os.getenv('GITHUB_TOKEN')
    anthropic_api_key = os.getenv('ANTHROPIC_API_KEY')

    if not github_token or not anthropic_api_key:
        logger.error("Please set GITHUB_TOKEN and ANTHROPIC_API_KEY in your .env file.")
        return

    # Download latest starred repos
    starred_repos = download_starred_repos(github_token)

    # Load predefined categories and existing repo-category mapping
    categories = load_json_file(CATEGORIES_FILE)
    repo_category_mapping = load_json_file(REPO_CATEGORY_MAPPING_FILE)

    # Remove unstarred repos from the mapping
    removed_repos = remove_unstarred_repos(starred_repos, repo_category_mapping)

    # Identify new or uncategorized repos
    repos_to_process = {
        full_name: repo for full_name, repo in starred_repos.items()
        if full_name not in repo_category_mapping
    }

    logger.info(f"Found {len(repos_to_process)} new or uncategorized repos to process")
    logger.info(f"Removed {len(removed_repos)} unstarred repos from categories")

    changes_made = len(repos_to_process) > 0 or len(removed_repos) > 0

    if repos_to_process:
        # Process repos in batches
        batch_size = 50
        repos_list = list(repos_to_process.values())
        with tqdm(total=len(repos_list), desc="Organizing repos") as pbar:
            for i in range(0, len(repos_list), batch_size):
                batch = {repo['FullName']: repo for repo in repos_list[i:i+batch_size]}
                
                new_mappings = organize_repos_with_claude(anthropic_api_key, batch, categories)
                
                # Update repo-category mapping
                for repo, repo_categories in new_mappings.items():
                    repo_category_mapping[repo] = repo_categories
                
                pbar.update(len(batch))

    if changes_made:
        # Save updated repo-category mapping
        save_json_file(repo_category_mapping, REPO_CATEGORY_MAPPING_FILE)

        # Update GitHub lists
        update_github_lists(github_token, categories, starred_repos, repo_category_mapping)
        logger.info("GitHub lists updated")

        # Update README
        update_readme(github_token, starred_repos, repo_category_mapping)
        logger.info("README updated")
    else:
        logger.info("No changes to process, skipping GitHub list and README update")

if __name__ == "__main__":
    main()