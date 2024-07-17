# GitHub Stars Organizer

![GitHub Stars](/header.png)

This project automatically organizes and categorizes starred GitHub repositories using the Anthropic Claude API. It updates a GitHub repository with categorized lists of starred repos and maintains an up-to-date README with statistics.

## Features

- Fetches starred repositories from GitHub
- Categorizes repositories using Anthropic's Claude AI
- Updates a GitHub repository with categorized lists
- Maintains a README with current statistics
- Handles addition of new starred repos and removal of unstarred ones

## Requirements

- Python 3.7+
- GitHub Personal Access Token
- Anthropic API Key

## Setup

1. Clone this repository
2. Install required packages: `pip install -r requirements.txt`
3. Create a `.env` file in the project root with the following content:
    ```
    GITHUB_TOKEN=your_github_token
    ANTHROPIC_API_KEY=your_anthropic_api_key
    GITHUB_REPO=your_username/your_repo_name
    ```
4. Create a `categories.json` file with your desired categories

## Limitations

If any modifications are made directly to the repository, the script will not repair or rebuild anything. If this happens, delete the `repo_category_mapping.json` and `starred_repos.json` files, and the script will rebuild everything from scratch.

## Usage

Run the script with:

```
python stars.py
```

Use the `--debug` flag for detailed logging:

```
python stars.py --debug
```

Modify the `categories.json` file to include any categories you wish to use.

## Warning

This script will consume a lot of tokens (since it uses Claude for categorization). You might want to consider running with a local LLM to avoid the cost. Use at your own discretion.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Transparency Disclaimer

[ai.collaboratedwith.me](ai.collaboratedwith.me) in creating this project.