# GitHub Stars Organizer

A streamlined tool that fetches your GitHub starred repositories, categorizes them using any OpenAI-compatible LLM (like Ollama, OpenRouter, or OpenAI), and generates a beautiful browsable HTML wiki page.

## ✨ Features

- **Fetch All Stars**: Downloads all your starred repos with descriptions, READMEs, and metadata
- **Smart Caching**: Everything is cached - gracefully handles interruptions and continues where it left off
- **LLM Categorization**: Uses any OpenAI-compatible LLM to intelligently categorize your stars
- **Browsable Wiki**: Generates a self-contained HTML page with sidebar navigation
- **Archive Support**: Automatically separates archived/deleted repos into a separate archive page
- **Progress Tracking**: Beautiful progress bars show you exactly what's happening
- **Graceful Exits**: Press Ctrl+C anytime - progress is saved automatically

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy the template and edit with your details:

```bash
cp config.yaml.template config.yaml
```

Edit `config.yaml` and add your:
- GitHub personal access token ([get one here](https://github.com/settings/tokens))
- LLM provider details (see examples below)

### 3. Run

```bash
python organize.py
```

The script will:
1. Fetch all your starred repos (with caching)
2. Categorize them using the LLM (with caching)
3. Generate HTML files in `output/`

Open `output/index.html` in your browser to browse your organized stars!

## 🎮 Usage Modes

### Full Run (Default)
```bash
python organize.py
```
Fetches all starred repos (uses cache for existing ones), categorizes uncategorized repos, and generates HTML.

### Recategorize from Scratch
```bash
python organize.py --recategorize
```
Clears all categories and re-categorizes all repos from cached star data. **Does not re-fetch from GitHub** - uses existing cache. Perfect when you want to try different categorization without hitting GitHub API limits.

### Incremental Update
```bash
python organize.py --update
```
Checks GitHub for new/removed stars:
- **Fetches** new repos you've starred since last run
- **Removes** repos you've unstarred
- **Preserves** existing categorizations
- **Only categorizes** newly added repos

This is the most efficient way to keep your wiki up-to-date!

## 🔧 Configuration Examples

### Using Ollama (Local)

```yaml
github_token: ghp_your_token_here

llm:
  base_url: http://localhost:11434/v1
  api_key: ollama
  model: llama3.1
```

### Using OpenRouter

```yaml
github_token: ghp_your_token_here

llm:
  base_url: https://openrouter.ai/api/v1
  api_key: sk-or-v1-your_key_here
  model: anthropic/claude-3.5-sonnet
```

### Using OpenAI

```yaml
github_token: ghp_your_token_here

llm:
  base_url: https://api.openai.com/v1
  api_key: sk-your_openai_key_here
  model: gpt-4o-mini
```

## 📁 Project Structure

```
github-stars-organizer/
├── organize.py              # Main script
├── config.yaml              # Your configuration (not in git)
├── config.yaml.template     # Configuration template
├── requirements.txt         # Python dependencies
├── cache/                   # Cached data (not in git)
│   ├── stars.json          # Cached repo data
│   └── categories.json     # Cached categorizations
└── output/                  # Generated HTML (not in git)
    ├── index.html          # Main browsable wiki
    └── archive.html        # Archived repos (if any)
```

## 🎯 How It Works

1. **Fetch Phase**: The script fetches all your starred repos from GitHub, including:
   - Name, description, URL
   - Programming language
   - Star count
   - README content (first 5000 chars)
   - Archive status
   - Everything is cached in `cache/stars.json`

2. **Categorize Phase**: For each uncategorized repo, the LLM:
   - Receives repo details and existing categories
   - Assigns to an existing category OR creates a new one
   - Generates a 2-3 sentence description
   - Results cached in `cache/categories.json`

3. **Generate Phase**: Creates self-contained HTML files:
   - `index.html` - Main wiki with sidebar navigation
   - `archive.html` - Archived/deleted repos (if any)
   - All CSS and JavaScript inline (no external dependencies)

## ⚡ Features in Detail

### Graceful Interruption
Press `Ctrl+C` at any time - the script saves progress immediately and exits cleanly. Next run continues from where you left off.

### Smart Caching
- Repos are only fetched once (unless you delete the cache)
- Categorization happens incrementally (only new repos)
- Re-running is fast if you just want to regenerate HTML

### Category Intelligence
The LLM is instructed to:
- Prefer existing categories when appropriate
- Only create new categories when necessary
- Use clear, descriptive category names
- This keeps your categories organized and prevents duplication

### Automatic Archive Detection
Repos marked as archived on GitHub are automatically separated into `archive.html` for reference.

## 🛠️ Advanced Usage

### Recategorize Everything
Use the built-in recategorize mode:
```bash
python organize.py --recategorize
```
This clears all categories and re-categorizes from cache (no GitHub API calls).

### Update Your Wiki Regularly
Set up a cron job or scheduled task:
```bash
# Update daily at 2 AM
0 2 * * * cd /path/to/github-stars-organizer && python organize.py --update
```

### Start Fresh (Nuclear Option)
Delete all cache and output, then re-run:
```bash
rm -rf cache/ output/
python organize.py
```

### Custom README Length
Edit `config.yaml`:
```yaml
readme_max_chars: 10000  # Send more README content to LLM
```

## 📋 Requirements

- Python 3.9+
- GitHub Personal Access Token
- Access to an OpenAI-compatible LLM API

## 🐛 Troubleshooting

**"GitHub API error"**: Check your GitHub token has the correct permissions
**"LLM error"**: Verify your LLM API is running and credentials are correct
**Script is slow**: First run takes time (fetching all stars + categorization). Subsequent runs are much faster due to caching.

## 📝 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Credits

Built with:
- [PyGithub](https://github.com/PyGithub/PyGithub) - GitHub API
- [OpenAI Python](https://github.com/openai/openai-python) - Universal LLM client
- [Rich](https://github.com/Textualize/rich) - Beautiful terminal output
- [PyYAML](https://github.com/yaml/pyyaml) - YAML configuration

---

**Note**: This is a complete rewrite of the original project, focusing on simplicity, reliability, and a better user experience.
