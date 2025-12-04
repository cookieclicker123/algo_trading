"""
Base repository - eliminates duplicated file I/O patterns.

This base class provides common file I/O operations used by repositories:
- Load JSON from file
- Save JSON to file
- Path management

Eliminates ~50 lines of duplicated code across 2+ repositories.
"""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
import aiofiles

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class BaseRepository:
    """
    Base class for repositories - handles common file I/O patterns.
    
    All repositories follow similar patterns:
    - Load JSON from file
    - Save JSON to file
    - Handle file not found
    - Handle JSON decode errors
    
    This base class eliminates code duplication while maintaining flexibility.
    """
    
    @staticmethod
    async def load_json_file(file_path: Path, default: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Load JSON data from file.
        
        Common pattern used by all repositories:
        - Check if file exists
        - Read file content
        - Parse JSON
        - Handle errors gracefully
        
        Args:
            file_path: Path to JSON file
            default: Default value to return if file doesn't exist or is invalid (defaults to empty list)
        
        Returns:
            List of dictionaries from JSON file, or default value
        
        Usage:
            articles = await BaseRepository.load_json_file(self.json_file)
        """
        if default is None:
            default = []
        
        if not file_path.exists():
            return default
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                if not content.strip():
                    return default
                return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(
                "Failed to load JSON file",
                file_path=str(file_path),
                error=str(e)
            )
            return default
        except Exception as e:
            logger.error(
                "Unexpected error loading JSON file",
                file_path=str(file_path),
                error=str(e),
                exc_info=True
            )
            return default
    
    @staticmethod
    async def save_json_file(
        file_path: Path,
        data: List[Dict[str, Any]],
        indent: int = 2,
        ensure_ascii: bool = False,
        default: Any = str
    ) -> None:
        """
        Save JSON data to file.
        
        Common pattern used by all repositories:
        - Create parent directories if needed
        - Write JSON to file
        - Handle errors
        
        Args:
            file_path: Path to JSON file
            data: List of dictionaries to save
            indent: JSON indentation (default: 2)
            ensure_ascii: Whether to ensure ASCII encoding (default: False)
            default: Default JSON encoder for non-serializable types (default: str)
        
        Raises:
            Exception: If file write fails
        
        Usage:
            await BaseRepository.save_json_file(self.json_file, articles)
        """
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                json_str = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, default=default)
                await f.write(json_str)
            
            logger.debug(
                "Saved JSON file",
                file_path=str(file_path),
                count=len(data)
            )
        except Exception as e:
            logger.error(
                "Failed to save JSON file",
                file_path=str(file_path),
                error=str(e),
                exc_info=True
            )
            raise
    
    @staticmethod
    def ensure_directory_exists(directory: Path) -> None:
        """
        Ensure directory exists, creating parent directories if needed.
        
        Args:
            directory: Directory path to ensure exists
        """
        directory.mkdir(parents=True, exist_ok=True)

