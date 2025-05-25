#!/usr/bin/env python3

import os
import sys
import shutil
import logging
import argparse
from pathlib import Path
import psutil
import time
from typing import Optional, List, Dict
from datetime import datetime
import zipfile
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RepositoryDeleter:
    """Forcefully delete a repository from the local machine."""
    
    def __init__(self, repo_path: str, force: bool = False):
        """
        Initialize the repository deleter.
        
        Args:
            repo_path (str): Path to the repository to delete
            force (bool): If True, bypass safety checks
        """
        self.repo_path = Path(repo_path).resolve()
        self.force = force
        
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
    
    def _check_process_locks(self) -> bool:
        """Check if any processes are locking files in the repository."""
        try:
            for proc in psutil.process_iter(['pid', 'name', 'open_files']):
                try:
                    for file in proc.open_files():
                        if str(self.repo_path) in str(file.path):
                            if not self.force:
                                logger.warning(f"Process {proc.pid} ({proc.name()}) is using files in the repository")
                                return False
                            else:
                                logger.info(f"Force killing process {proc.pid} ({proc.name()})")
                                proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return True
        except Exception as e:
            logger.error(f"Error checking process locks: {str(e)}")
            return False
    
    def _kill_git_processes(self):
        """Force kill any git processes and handle processes locking the repository."""
        try:
            killed_processes = set()
            # First pass: kill git processes
            for proc in psutil.process_iter(['pid', 'name', 'open_files']):
                try:
                    if 'git' in proc.name().lower():
                        logger.info(f"Killing git process: {proc.pid} ({proc.name()})")
                        proc.kill()
                        killed_processes.add(proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Second pass: handle any processes locking files
            for proc in psutil.process_iter(['pid', 'name', 'open_files']):
                try:
                    if proc.pid in killed_processes:
                        continue
                    for file in proc.open_files():
                        if str(self.repo_path) in str(file.path):
                            logger.info(f"Killing process locking repository: {proc.pid} ({proc.name()})")
                            proc.kill()
                            killed_processes.add(proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Give processes time to terminate
            time.sleep(1)

            # Final pass: ensure all processes are dead
            for pid in killed_processes:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        except Exception as e:
            logger.error(f"Error killing processes: {str(e)}")
    
    def _remove_readonly(self, func, path, _):
        """Remove readonly attribute from files and force delete if needed."""
        try:
            # Try to change permissions to full access
            os.chmod(path, 0o777)
            try:
                func(path)
            except Exception as e:
                logger.warning(f"Failed to remove {path} normally: {str(e)}")
                # Force delete using different methods
                try:
                    if os.path.isfile(path):
                        os.unlink(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                except Exception as e2:
                    logger.warning(f"Force delete failed for {path}: {str(e2)}")
                    # Last resort: try to rename and delete
                    try:
                        temp_path = str(path) + '.deleteme'
                        os.rename(path, temp_path)
                        if os.path.isfile(temp_path):
                            os.unlink(temp_path)
                        elif os.path.isdir(temp_path):
                            shutil.rmtree(temp_path, ignore_errors=True)
                    except Exception as e3:
                        logger.error(f"All deletion attempts failed for {path}: {str(e3)}")
        except Exception as e:
            logger.error(f"Error handling {path}: {str(e)}")
            if self.force:
                try:
                    # Try direct deletion as last resort
                    if os.path.isfile(path):
                        os.unlink(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                except Exception as e2:
                    logger.error(f"Final force delete attempt failed for {path}: {str(e2)}")
    
    def delete_repository(self) -> bool:
        """
        Force delete the repository and all its contents.
        
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            logger.info(f"Preparing to force delete repository: {self.repo_path}")
            
            # Always kill processes in force mode
            self._kill_git_processes()
            
            # Try to remove the .git directory first
            git_dir = self.repo_path / '.git'
            if git_dir.exists():
                logger.info("Removing .git directory...")
                try:
                    shutil.rmtree(git_dir, onerror=self._remove_readonly)
                except Exception as e:
                    logger.warning(f"Error removing .git directory: {str(e)}")
                    # Continue with deletion even if .git removal fails
            
            # Remove the rest of the repository
            logger.info("Removing repository files...")
            try:
                shutil.rmtree(self.repo_path, onerror=self._remove_readonly)
            except Exception as e:
                logger.warning(f"Error during repository removal: {str(e)}")
                # Try one final time with direct deletion
                try:
                    for root, dirs, files in os.walk(self.repo_path, topdown=False):
                        for name in files:
                            try:
                                os.chmod(os.path.join(root, name), 0o777)
                                os.unlink(os.path.join(root, name))
                            except Exception:
                                pass
                        for name in dirs:
                            try:
                                os.chmod(os.path.join(root, name), 0o777)
                                os.rmdir(os.path.join(root, name))
                            except Exception:
                                pass
                    os.rmdir(self.repo_path)
                except Exception as e2:
                    logger.error(f"Final deletion attempt failed: {str(e2)}")
                    return False
            
            # Verify deletion
            if self.repo_path.exists():
                logger.error("Repository still exists after deletion attempts")
                return False
                
            logger.info("Repository deleted successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Unexpected error during deletion: {str(e)}")
            return False

class RepoArchiver:
    """Archive important documentation folders before repository deletion."""
    
    def __init__(self, repo_path: str, force: bool = False):
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
        
        self.repo_name = self.repo_path.name
        # Create Archives directory in the parent directory of the repository
        self.archive_base_dir = self.repo_path.parent / "Archives" / self.repo_name
        self.archive_base_dir.mkdir(parents=True, exist_ok=True)
        self.force = force
        
        # Initialize repository deleter
        self.deleter = RepositoryDeleter(repo_path, force)
        
        # Folders to archive
        self.docs_folders = [
            "API Documentation",
            "Classifier",
            "Logic Understanding",
            "UAT Documentation"
        ]
        
        # Load or create version tracking
        self.version_file = self.archive_base_dir / "versions.json"
        self.versions = self._load_versions()
    
    def _load_versions(self) -> Dict[str, int]:
        """Load version tracking information."""
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Error reading versions file, starting fresh")
                return {}
        return {}
    
    def _save_versions(self):
        """Save version tracking information."""
        with open(self.version_file, 'w') as f:
            json.dump(self.versions, f, indent=2)
    
    def _get_next_version(self, folder_name: str) -> int:
        """Get the next version number for a folder."""
        current_version = self.versions.get(folder_name, 0)
        next_version = current_version + 1
        self.versions[folder_name] = next_version
        return next_version
    
    def _create_archive_name(self, folder_name: str, version: int) -> str:
        """Create archive filename with version and timestamp."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{folder_name}_v{version}_{timestamp}.zip"
    
    def _archive_folder(self, folder_name: str) -> bool:
        """Archive a single documentation folder if it exists."""
        folder_path = self.repo_path / folder_name
        if not folder_path.exists():
            logger.info(f"Folder not found: {folder_name}")
            return False
        
        # Get next version number
        version = self._get_next_version(folder_name)
        archive_name = self._create_archive_name(folder_name, version)
        archive_path = self.archive_base_dir / archive_name
        
        try:
            # Create zip archive
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(folder_path)
                        zipf.write(file_path, arcname)
            
            logger.info(f"Archived {folder_name} to {archive_name}")
            self._save_versions()
            return True
            
        except Exception as e:
            logger.error(f"Error archiving {folder_name}: {str(e)}")
            if archive_path.exists():
                archive_path.unlink()
            return False
    
    def archive_documentation(self) -> List[str]:
        """Archive all documentation folders."""
        archived = []
        for folder in self.docs_folders:
            if self._archive_folder(folder):
                archived.append(folder)
        return archived
    
    def delete_repository(self) -> bool:
        """Delete the repository after archiving using the robust deletion process."""
        try:
            return self.deleter.delete_repository()
        except Exception as e:
            logger.error(f"Error deleting repository: {str(e)}")
            raise

def main():
    """Main function to archive and delete repository."""
    parser = argparse.ArgumentParser(
        description='Archive documentation folders and delete repository',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Archive and delete repository
  python repo_delete.py /path/to/repo
  
  # Archive only (dry run)
  python repo_delete.py /path/to/repo --dry-run
  
  # Force delete (bypass safety checks)
  python repo_delete.py /path/to/repo --force
        """
    )
    
    parser.add_argument('repo_path', help='Path to the repository')
    parser.add_argument('--dry-run', action='store_true',
                      help='Archive folders without deleting repository')
    parser.add_argument('--force', '-f', action='store_true',
                      help='Force delete, bypassing safety checks')
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        archiver = RepoArchiver(args.repo_path, args.force)
        
        # Archive documentation folders
        archived = archiver.archive_documentation()
        
        if not archived:
            logger.warning("No documentation folders were archived")
        
        # Delete repository unless dry run
        if not args.dry_run:
            success = archiver.delete_repository()
            if success:
                print("\nRepository Deleted Successfully!")
            else:
                print("\nRepository deletion failed. Try using --force if needed.")
                sys.exit(1)
        else:
            print("\nDry Run Completed!")
        
        print("=" * 50)
        print(f"Archives location: {archiver.archive_base_dir.parent}")  # Show the parent Archives directory
        print(f"Repository archives: {archiver.archive_base_dir}")  # Show the specific repository's archive directory
        print("\nArchived folders:")
        for folder in archived:
            print(f"• {folder}")
        if args.dry_run:
            print("\nRepository was NOT deleted (dry run)")
        
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error processing repository: {str(e)}")
        raise

if __name__ == '__main__':
    main() 