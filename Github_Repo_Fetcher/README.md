# GitHub Repository Cloner

A Python script to clone both public and private GitHub repositories with authentication support.

## Prerequisites

- Python 3.6 or higher
- Git installed on your system
- (Optional) GitHub Personal Access Token for private repositories

## Installation

1. Clone this repository or download the files
2. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

To clone a public repository:
```bash
python github_repo_cloner.py https://github.com/username/repository.git
```

### Advanced Usage

1. Clone to a specific directory:
```bash
python github_repo_cloner.py https://github.com/username/repository.git --target-dir /path/to/directory
```

2. Clone a private repository using a token:
```bash
python github_repo_cloner.py https://github.com/username/private-repo.git --token your_github_token
```

3. Using environment variables:
   - Create a `.env` file in the same directory
   - Add your GitHub token:
   ```
   GITHUB_TOKEN=your_github_token
   ```
   - The script will automatically use this token when cloning private repositories

## Getting a GitHub Token

1. Go to GitHub Settings > Developer Settings > Personal Access Tokens
2. Generate a new token with the `repo` scope
3. Copy the token and use it with the `--token` argument or in the `.env` file

## Error Handling

The script includes error handling for common issues:
- Invalid repository URLs
- Authentication failures
- Network issues
- Permission problems

If an error occurs, the script will display an appropriate error message and exit with status code 1. 