#!/usr/bin/env python3

import os
import sys
import re
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse
import logging
from git import Repo, GitCommandError
from github import Github, GithubException
from dotenv import load_dotenv
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class GitHubRepoCloner:
    """Class to handle GitHub repository cloning operations."""
    
    def __init__(self):
        """Initialize the GitHub repository cloner."""
        self.token = self._get_token()
        self.github = Github(self.token) if self.token else None

    def _get_token(self) -> Optional[str]:
        """
        Get GitHub token from environment or .env file.
        
        Returns:
            Optional[str]: GitHub token if found, None otherwise
        """
        # Get the directory containing the script
        script_dir = Path(__file__).parent.absolute()
        env_path = script_dir / '.env'
        
        if not env_path.exists():
            logger.warning(f".env file not found at: {env_path}")
            return None
        
        # Load environment variables from .env file
        load_dotenv(env_path)
        
        # Try to get token from environment
        token = os.getenv('GITHUB_TOKEN')
        
        if not token:
            logger.warning("No GitHub token found in .env file. Private repositories may not be accessible.")
            return None
            
        # Validate token format
        if not token.startswith('ghp_') and not token.startswith('github_pat_'):
            logger.warning("GitHub token doesn't appear to be in the correct format")
            return None
            
        logger.info("Successfully loaded GitHub token from .env file")
        return token

    def _validate_repo_url(self, url: str) -> Tuple[bool, str]:
        """
        Validate the GitHub repository URL.
        
        Args:
            url (str): Repository URL to validate
            
        Returns:
            Tuple[bool, str]: (is_valid, error_message)
        """
        if not url:
            return False, "Repository URL cannot be empty"
            
        # Check if it's a valid URL
        try:
            parsed = urlparse(url)
            if not all([parsed.scheme, parsed.netloc]):
                return False, "Invalid URL format"
                
            # Check if it's a GitHub URL
            if not parsed.netloc.endswith('github.com'):
                return False, "URL must be a GitHub repository"
                
            # Check if it has a valid repository path
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) < 2:
                return False, "Invalid repository path"
                
            return True, ""
            
        except Exception as e:
            return False, f"URL validation error: {str(e)}"

    def _get_repo_info(self, repo_url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract owner and repository name from URL.
        
        Args:
            repo_url (str): GitHub repository URL
            
        Returns:
            Tuple[Optional[str], Optional[str]]: (owner, repo_name)
        """
        try:
            # Remove .git extension if present
            repo_url = repo_url.replace('.git', '')
            # Extract the last two parts of the path
            parts = urlparse(repo_url).path.strip('/').split('/')
            if len(parts) >= 2:
                return parts[-2], parts[-1]
        except Exception as e:
            logger.error(f"Error extracting repo info: {str(e)}")
        return None, None

    def _check_repo_access(self, owner: str, repo_name: str) -> bool:
        """
        Check if the repository is accessible.
        
        Args:
            owner (str): Repository owner
            repo_name (str): Repository name
            
        Returns:
            bool: True if repository is accessible
        """
        if not self.github:
            return True  # Can't check without token, will try to clone anyway
            
        try:
            repo = self.github.get_repo(f"{owner}/{repo_name}")
            return True
        except GithubException as e:
            if e.status == 404:
                logger.error("Repository not found")
            elif e.status == 403:
                logger.error("Access denied. Check your token permissions")
            else:
                logger.error(f"GitHub API error: {str(e)}")
            return False

    def clone_repository(self, repo_url: str, target_dir: str) -> bool:
        """
        Clone a GitHub repository to the specified directory.
        
        Args:
            repo_url (str): The URL of the GitHub repository
            target_dir (str): The directory where the repository should be cloned
            
        Returns:
            bool: True if cloning was successful
        """
        # Validate URL
        is_valid, error_msg = self._validate_repo_url(repo_url)
        if not is_valid:
            logger.error(error_msg)
            return False

        # Get repository info
        owner, repo_name = self._get_repo_info(repo_url)
        if not owner or not repo_name:
            logger.error("Could not extract repository information from URL")
            return False

        # Check repository access
        if not self._check_repo_access(owner, repo_name):
            return False

        try:
            # Create target directory if it doesn't exist
            target_path = Path(target_dir) / repo_name
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Prepare repository URL with token if available
            if self.token and repo_url.startswith('https://'):
                repo_url = repo_url.replace('https://', f'https://{self.token}@')

            logger.info(f"Cloning repository to: {target_path}")
            Repo.clone_from(repo_url, str(target_path))
            logger.info(f"Successfully cloned repository to {target_path}")
            return True

        except GitCommandError as e:
            logger.error(f"Git error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return False

def main():
    """Main function to handle command line arguments and execute cloning."""
    parser = argparse.ArgumentParser(
        description='Clone GitHub repositories (public or private)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        'repo_url',
        help='URL of the GitHub repository to clone (e.g., https://github.com/username/repo.git)'
    )
    
    parser.add_argument(
        '--target-dir',
        default='.',
        help='Target directory for cloning (default: current directory)'
    )
    
    parser.add_argument(
        '--token',
        help='GitHub personal access token (optional, will override token from .env file)'
    )
    
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Initialize cloner
    cloner = GitHubRepoCloner()
    
    # Override token if provided in arguments
    if args.token:
        if not args.token.startswith('ghp_') and not args.token.startswith('github_pat_'):
            logger.error("Invalid GitHub token format. Token should start with 'ghp_' or 'github_pat_'")
            sys.exit(1)
        cloner.token = args.token
        logger.info("Using GitHub token from command line arguments")

    # Clone repository
    success = cloner.clone_repository(args.repo_url, args.target_dir)
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main() 