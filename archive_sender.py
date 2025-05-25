#!/usr/bin/env python3

import os
import sys
import json
import logging
import requests
import argparse
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import zipfile
import shutil
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('archive_sender.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class ArchiveSender:
    """Handle sending of archived documentation folders via webhook."""
    
    def __init__(self, repo_name: str, webhook_url: Optional[str] = None, archive_dir: Optional[str] = None, repo_path: Optional[str] = None, reference_id: Optional[str] = None):
        """
        Initialize the archive sender.
        
        Args:
            repo_name (str): Name of the repository
            webhook_url (str, optional): URL to send the archives to. Will only be used if ARCHIVE_WEBHOOK_URL is not in .env
            archive_dir (str, optional): Directory containing archives. If None, will look in repo_path.parent/Archives/repo_name
            repo_path (str, optional): Path to the repository. Required if archive_dir is not provided
            reference_id (str, optional): Reference ID to include in archive metadata
        """
        # Get webhook URL from environment first, then fallback to parameter
        self.webhook_url = os.getenv('ARCHIVE_WEBHOOK_URL') or webhook_url
        if not self.webhook_url:
            raise ValueError("No webhook URL found in environment variables (ARCHIVE_WEBHOOK_URL) or provided as parameter")
            
        self.repo_name = repo_name
        self.reference_id = reference_id
        
        # Set up archive directory
        if archive_dir:
            self.archive_dir = Path(archive_dir).resolve()
        else:
            if not repo_path:
                raise ValueError("repo_path is required when archive_dir is not provided")
            # Use the exact path format: repo_path.parent/Archives/repo_name
            self.archive_dir = Path(repo_path).parent / "Archives" / repo_name
        
        if not self.archive_dir.exists():
            raise ValueError(f"Archive directory not found: {self.archive_dir}. Please ensure the archives are in {self.archive_dir}")
    
    def _create_archive_package(self) -> Optional[Path]:
        """
        Create a zip package of all documentation archives.
        
        Returns:
            Optional[Path]: Path to the created zip file, or None if creation failed
        """
        try:
            # Create a temporary directory for packaging
            temp_dir = Path.cwd() / "temp_archives"
            temp_dir.mkdir(exist_ok=True)
            
            # Create a zip file with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"{self.repo_name}_documentation_{timestamp}.zip"
            zip_path = temp_dir / zip_filename
            
            # Create the zip file
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add all files from the archive directory
                for root, _, files in os.walk(self.archive_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(self.archive_dir)
                        zipf.write(file_path, arcname)
                
                # Add a manifest file
                manifest = {
                    'repository': self.repo_name,
                    'timestamp': timestamp,
                    'reference_id': self.reference_id,
                    'contents': [f.name for f in self.archive_dir.glob('*') if f.is_file()]
                }
                manifest_path = temp_dir / 'manifest.json'
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f, indent=2)
                zipf.write(manifest_path, 'manifest.json')
                manifest_path.unlink()
            
            return zip_path
            
        except Exception as e:
            logger.error(f"Error creating archive package: {str(e)}")
            return None
    
    def _cleanup_temp_files(self, zip_path: Path):
        """Clean up temporary files after sending."""
        try:
            if zip_path.exists():
                zip_path.unlink()
            temp_dir = zip_path.parent
            if temp_dir.exists():
                temp_dir.rmdir()
        except Exception as e:
            logger.warning(f"Error cleaning up temporary files: {str(e)}")
    
    def _prepare_headers(self) -> Dict[str, str]:
        """Prepare headers for the webhook request."""
        headers = {
            'Content-Type': 'application/zip',
            'X-Repository-Name': self.repo_name,
            'X-Timestamp': datetime.now().isoformat()
        }
        
        if self.reference_id:
            headers['X-Reference-ID'] = self.reference_id
            
        return headers
    
    def send_archives(self) -> bool:
        """
        Send archived documentation folders via webhook.
        
        Returns:
            bool: True if sending was successful
        """
        try:
            # Create archive package
            zip_path = self._create_archive_package()
            if not zip_path:
                return False
            
            # Prepare headers
            headers = self._prepare_headers()
            
            # Send the archive
            logger.info(f"Sending archives to {self.webhook_url}")
            with open(zip_path, 'rb') as f:
                response = requests.post(
                    self.webhook_url,
                    headers=headers,
                    data=f,
                    timeout=300  # 5-minute timeout for large files
                )
            
            # Check response
            if response.status_code == 200:
                logger.info("Archives sent successfully")
                self._cleanup_temp_files(zip_path)
                return True
            else:
                logger.error(f"Failed to send archives. Status code: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending archives: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return False
        finally:
            # Clean up temporary files
            if 'zip_path' in locals():
                self._cleanup_temp_files(zip_path)

def main():
    """Main function to handle command line arguments and send archives."""
    parser = argparse.ArgumentParser(
        description='Send repository documentation archives via webhook',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send archives using webhook URL from .env file
  python archive_sender.py --repo my-repo --repo-path /path/to/repo --reference-id REF123
  
  # Send archives using specific webhook URL (only if ARCHIVE_WEBHOOK_URL is not in .env)
  python archive_sender.py --webhook-url https://your-webhook-url --repo my-repo --repo-path /path/to/repo --reference-id REF123
  
  # Send archives from specific directory
  python archive_sender.py --repo my-repo --archive-dir /path/to/archives --reference-id REF123
        """
    )
    
    parser.add_argument('--webhook-url',
                      help='URL to send the archives to (optional, will use ARCHIVE_WEBHOOK_URL from .env if available)')
    parser.add_argument('--repo', required=True,
                      help='Name of the repository')
    parser.add_argument('--repo-path',
                      help='Path to the repository (required if archive-dir is not provided)')
    parser.add_argument('--archive-dir',
                      help='Directory containing archives (optional)')
    parser.add_argument('--reference-id',
                      help='Reference ID to include in archive metadata')
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        sender = ArchiveSender(args.repo, args.webhook_url, args.archive_dir, args.repo_path, args.reference_id)
        success = sender.send_archives()
        sys.exit(0 if success else 1)
        
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 