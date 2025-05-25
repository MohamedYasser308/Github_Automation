#!/usr/bin/env python3

import os
import sys
import json
import logging
import subprocess
import argparse
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
from flask import Flask, request, jsonify
import hmac
import hashlib
from dotenv import load_dotenv
import threading
import queue
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('repo_processor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Load environment variables
load_dotenv()

# Get GitHub webhook secret from environment (optional)
GITHUB_WEBHOOK_SECRET = os.getenv('GITHUB_WEBHOOK_SECRET')

# Queue for processing repositories
repo_queue = queue.Queue()

class RepositoryProcessor:
    """Handle the processing of a repository through all stages."""
    
    def __init__(self, repo_url: str, target_dir: str = '.', archive_webhook_url: Optional[str] = None, reference_id: Optional[str] = None):
        """
        Initialize the repository processor.
        
        Args:
            repo_url (str): URL of the GitHub repository to process
            target_dir (str): Directory where the repository should be cloned
            archive_webhook_url (str, optional): URL to send archives to after processing. Will only be used if ARCHIVE_WEBHOOK_URL is not in .env
            reference_id (str, optional): Reference ID to include in archive metadata
        """
        self.repo_url = repo_url
        self.target_dir = Path(target_dir).resolve()
        self.repo_name = self._extract_repo_name(repo_url)
        self.repo_path = self.target_dir / self.repo_name
        # Get webhook URL from environment first, then fallback to parameter
        self.archive_webhook_url = os.getenv('ARCHIVE_WEBHOOK_URL') or archive_webhook_url
        self.reference_id = reference_id
        self.scripts = [
            'github_repo_cloner.py',
            'file_classifier.py',
            'project_documentation_generator.py',
            'uat_documentation_generator.py',
            'api_documentation_generator.py',
            'repo_delete.py'
        ]
        self.status: Dict[str, str] = {}
        self.start_time = None
        self.end_time = None
    
    def _extract_repo_name(self, url: str) -> str:
        """Extract repository name from URL."""
        return url.rstrip('/').split('/')[-1].replace('.git', '')
    
    def _verify_github_signature(self, payload: bytes, signature: str) -> bool:
        """Verify GitHub webhook signature if secret is configured."""
        if not GITHUB_WEBHOOK_SECRET:
            logger.info("No webhook secret configured, skipping signature verification")
            return True
            
        if not signature:
            logger.warning("No signature provided in request")
            return True
            
        try:
            expected_signature = hmac.new(
                GITHUB_WEBHOOK_SECRET.encode(),
                payload,
                hashlib.sha1
            ).hexdigest()
            
            return hmac.compare_digest(f"sha1={expected_signature}", signature)
        except Exception as e:
            logger.warning(f"Error verifying signature: {str(e)}")
            return True
    
    def _run_script(self, script_name: str, *args) -> bool:
        """
        Run a single script with arguments.
        
        Args:
            script_name (str): Name of the script to run
            *args: Additional arguments to pass to the script
            
        Returns:
            bool: True if script executed successfully
        """
        script_path = Path(__file__).parent / script_name
        if not script_path.exists():
            logger.error(f"Script not found: {script_name}")
            return False
        
        cmd = [sys.executable, str(script_path)] + list(args)
        logger.info(f"Running {script_name} with args: {args}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"{script_name} output: {result.stdout}")
            if result.stderr:
                logger.warning(f"{script_name} stderr: {result.stderr}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error running {script_name}: {str(e)}")
            logger.error(f"Script output: {e.stdout}")
            logger.error(f"Script error: {e.stderr}")
            return False
    
    def _send_archives(self) -> bool:
        """Send archives via webhook if webhook URL is configured."""
        if not self.archive_webhook_url:
            logger.info("No archive webhook URL configured, skipping archive sending")
            return True
            
        try:
            from archive_sender import ArchiveSender
            sender = ArchiveSender(self.repo_name, self.archive_webhook_url, repo_path=str(self.repo_path), reference_id=self.reference_id)
            success = sender.send_archives()
            self.status['archive_send'] = 'completed' if success else 'failed'
            return success
        except Exception as e:
            logger.error(f"Error sending archives: {str(e)}")
            self.status['archive_send'] = 'failed'
            return False
    
    def process_repository(self) -> bool:
        """
        Process the repository through all stages.
        If API documentation generation fails, it will be skipped and the process continues with deletion.
        
        Returns:
            bool: True if all critical stages completed successfully
        """
        self.start_time = datetime.now()
        success = True
        
        try:
            # Stage 1: Clone repository
            logger.info(f"Starting repository processing: {self.repo_url}")
            if not self._run_script('github_repo_cloner.py', self.repo_url, '--target-dir', str(self.target_dir)):
                self.status['clone'] = 'failed'
                return False
            self.status['clone'] = 'completed'
            
            # Stage 2: Classify files
            if not self._run_script('file_classifier.py', str(self.repo_path)):
                self.status['classify'] = 'failed'
                return False
            self.status['classify'] = 'completed'
            
            # Stage 3: Generate project documentation
            if not self._run_script('project_documentation_generator.py', str(self.repo_path)):
                self.status['project_docs'] = 'failed'
                return False
            self.status['project_docs'] = 'completed'
            
            # Stage 4: Generate UAT documentation
            if not self._run_script('uat_documentation_generator.py', str(self.repo_path)):
                self.status['uat_docs'] = 'failed'
                return False
            self.status['uat_docs'] = 'completed'
            
            # Stage 5: Generate API documentation (non-critical stage)
            try:
                if not self._run_script('api_documentation_generator.py', str(self.repo_path)):
                    logger.warning("API documentation generation failed, continuing with archive sending")
                    self.status['api_docs'] = 'skipped'
                else:
                    self.status['api_docs'] = 'completed'
            except Exception as e:
                logger.error(f"Error during API documentation generation: {str(e)}")
                logger.warning("Skipping API documentation generation and continuing with archive sending")
                self.status['api_docs'] = 'skipped'
            
            # Stage 6: Send archives (before deletion)
            if self.archive_webhook_url:
                try:
                    # Run archive_sender.py before repo_delete.py
                    archive_sender_args = [
                        'archive_sender.py',
                        '--repo', self.repo_name,
                        '--repo-path', str(self.repo_path)
                    ]
                    if self.reference_id:
                        archive_sender_args.extend(['--reference-id', self.reference_id])
                    
                    if not self._run_script(*archive_sender_args):
                        logger.error("Failed to send archives, aborting repository deletion")
                        self.status['archive_send'] = 'failed'
                        return False
                    else:
                        self.status['archive_send'] = 'completed'
                except Exception as e:
                    logger.error(f"Error sending archives: {str(e)}")
                    self.status['archive_send'] = 'failed'
                    return False
            else:
                logger.info("No archive webhook URL configured, skipping archive sending")
                self.status['archive_send'] = 'skipped'
            
            # Stage 7: Delete repository (critical stage, only after successful archive sending)
            if not self._run_script('repo_delete.py', str(self.repo_path), '--force'):
                self.status['delete'] = 'failed'
                return False
            self.status['delete'] = 'completed'
            
            self.end_time = datetime.now()
            logger.info("Repository processing completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error processing repository: {str(e)}")
            return False
    
    def get_status(self) -> Dict:
        """Get the current status of the repository processing."""
        return {
            'repository': self.repo_url,
            'status': self.status,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration': str(self.end_time - self.start_time) if (self.start_time and self.end_time) else None
        }

def process_queue():
    """Process repositories in the queue."""
    while True:
        try:
            processor = repo_queue.get()
            if processor is None:
                break
            processor.process_repository()
            repo_queue.task_done()
        except Exception as e:
            logger.error(f"Error processing queue: {str(e)}")
        time.sleep(1)

# Start queue processor thread
queue_thread = threading.Thread(target=process_queue, daemon=True)
queue_thread.start()

@app.route('/webhook/<path:github_url>', methods=['POST'])
def github_webhook(github_url: str):
    """
    Handle webhook requests with GitHub URL in the path.
    
    Args:
        github_url (str): The GitHub repository URL to process, optionally followed by /Reference_ID
    """
    if request.method != 'POST':
        return jsonify({'error': 'Method not allowed'}), 405
    
    try:
        # Verify signature only if secret is configured and signature is provided
        if GITHUB_WEBHOOK_SECRET and request.headers.get('X-Hub-Signature'):
            if not RepositoryProcessor._verify_github_signature(
                request.get_data(),
                request.headers.get('X-Hub-Signature')
            ):
                logger.warning("Invalid signature received, but continuing with processing")
        
        # Split the URL to get the GitHub URL and Reference ID
        parts = github_url.split('/')
        reference_id = parts[-1] if len(parts) > 2 and not parts[-1].endswith('.git') else None
        github_url = '/'.join(parts[:-1]) if reference_id else github_url
        
        # Validate GitHub URL format
        if not github_url.startswith(('https://github.com/', 'http://github.com/')):
            return jsonify({
                'error': 'Invalid GitHub URL format',
                'message': 'URL must start with https://github.com/ or http://github.com/'
            }), 400
        
        # Clean up the URL
        github_url = github_url.rstrip('/')
        if not github_url.endswith('.git'):
            github_url += '.git'
        
        # Get archive webhook URL from environment first, then from request headers
        archive_webhook = os.getenv('ARCHIVE_WEBHOOK_URL') or request.headers.get('X-Archive-Webhook')
        
        # Create processor and add to queue
        processor = RepositoryProcessor(github_url, archive_webhook_url=archive_webhook, reference_id=reference_id)
        repo_queue.put(processor)
        
        return jsonify({
            'message': 'Repository processing queued',
            'repository': github_url,
            'reference_id': reference_id,
            'status': 'queued'
        }), 202
        
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Get status of all repository processing tasks."""
    try:
        statuses = []
        for processor in list(repo_queue.queue):
            statuses.append(processor.get_status())
        return jsonify({'statuses': statuses})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def main():
    """Main function to start the webhook server."""
    parser = argparse.ArgumentParser(
        description='Process GitHub repositories through documentation and analysis pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start webhook server
  python repo_processor.py --port 5000 --archive-webhook https://your-webhook-url
  
  # Process a single repository
  python repo_processor.py --repo https://github.com/username/repo.git --archive-webhook https://your-webhook-url
        """
    )
    
    parser.add_argument('--port', type=int, default=5000,
                      help='Port to run the webhook server on (default: 5000)')
    parser.add_argument('--repo', help='Process a single repository URL')
    parser.add_argument('--target-dir', default='.',
                      help='Directory where repositories should be cloned')
    parser.add_argument('--archive-webhook',
                      help='Webhook URL to send archives to after processing')
    parser.add_argument('--reference-id',
                      help='Reference ID to include in archive metadata')
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        if args.repo:
            # Process single repository
            processor = RepositoryProcessor(args.repo, args.target_dir, args.archive_webhook, args.reference_id)
            success = processor.process_repository()
            sys.exit(0 if success else 1)
        else:
            # Start webhook server
            logger.info(f"Starting webhook server on port {args.port}")
            app.run(host='0.0.0.0', port=args.port)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        repo_queue.put(None)  # Signal queue processor to stop
        queue_thread.join()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 