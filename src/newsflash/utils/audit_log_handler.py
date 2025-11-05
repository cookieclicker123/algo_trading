"""
Audit Log Handler for capturing all terminal logs to structured files.

Organizes logs by year/month/week/day in audit_logs directory.
Appends to existing files to support continuation after server restarts.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class AuditLogFileHandler(logging.Handler):
    """
    Custom logging handler that writes all logs to files organized by date.
    
    Structure: audit_logs/YYYY/MM/week_XX/YYYY-MM-DD.log
    Appends to existing files to support continuation after restarts.
    """
    
    def __init__(self, base_dir: str = "tmp/audit_logs"):
        """
        Initialize the audit log handler.
        
        Args:
            base_dir: Base directory for audit logs
        """
        super().__init__()
        # Set to lowest level to capture all logs
        self.setLevel(logging.DEBUG)
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.current_file = None
        self.current_file_path = None
    
    def _get_daily_file_path(self, timestamp: datetime) -> Path:
        """
        Get the file path for a specific date.
        
        Args:
            timestamp: Timestamp for the log entry
            
        Returns:
            Path to the log file for that date
        """
        year = timestamp.year
        month = timestamp.month
        week_num = timestamp.isocalendar()[1]
        day_str = timestamp.strftime("%Y-%m-%d")
        
        # Create directory structure: YYYY/MM/week_XX/
        dir_path = self.base_dir / str(year) / f"{month:02d}" / f"week_{week_num:02d}"
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Return file path: YYYY-MM-DD.log
        return dir_path / f"{day_str}.log"
    
    def emit(self, record: logging.LogRecord):
        """
        Emit a log record to the appropriate file.
        
        Args:
            record: The log record to emit
        """
        try:
            # Get timestamp from record
            timestamp = datetime.fromtimestamp(record.created)
            
            # Get the file path for this date
            file_path = self._get_daily_file_path(timestamp)
            
            # Open file in append mode (creates file if it doesn't exist)
            # Use 'a' mode to append, which supports continuation after restarts
            try:
                with open(file_path, 'a', encoding='utf-8') as f:
                    # Format the log entry
                    log_entry = self._format_log_entry(record, timestamp)
                    f.write(log_entry + '\n')
                    f.flush()  # Ensure immediate write
            except Exception as e:
                # Fallback: try to log error, but don't crash
                print(f"Error writing to audit log file {file_path}: {e}", file=sys.stderr)
                
        except Exception as e:
            # Don't let audit logging errors crash the application
            # Just print to stderr as fallback
            try:
                print(f"Error in audit log handler: {e}", file=sys.stderr)
            except:
                pass  # If even stderr fails, give up
    
    def _format_log_entry(self, record: logging.LogRecord, timestamp: datetime) -> str:
        """
        Format a log record for writing to file.
        
        Args:
            record: The log record
            timestamp: The timestamp for the entry
            
        Returns:
            Formatted log entry as string (JSON if possible, otherwise plain text)
        """
        try:
            # Try to parse the message as JSON (structlog outputs JSON)
            message = record.getMessage()
            try:
                # If it's already JSON, parse and enhance it
                log_data = json.loads(message)
            except (json.JSONDecodeError, ValueError):
                # If not JSON, create a structured entry from the record
                log_data = {
                    "timestamp": timestamp.isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno
                }
                
                # Add exception info if present
                if record.exc_info:
                    import traceback
                    log_data["exception"] = "".join(traceback.format_exception(*record.exc_info))
            
            # Ensure timestamp is in the log data
            if "timestamp" not in log_data or not log_data["timestamp"]:
                log_data["timestamp"] = timestamp.isoformat()
            
            # Return as JSON string
            return json.dumps(log_data, ensure_ascii=False)
            
        except Exception as e:
            # Fallback to plain text if JSON formatting fails
            return json.dumps({
                "timestamp": timestamp.isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "format_error": str(e)
            }, ensure_ascii=False)
    
    def flush(self):
        """Flush any buffered output."""
        # Files are opened with flush on each write, but we can implement
        # additional flushing logic here if needed
        pass


def setup_audit_logging(base_dir: str = "tmp/audit_logs") -> AuditLogFileHandler:
    """
    Set up audit logging and add handler to root logger.
    Prevents duplicate handlers if called multiple times.
    
    Args:
        base_dir: Base directory for audit logs
        
    Returns:
        The configured audit log handler
    """
    root_logger = logging.getLogger()
    
    # Check if audit handler already exists to prevent duplicates
    for existing_handler in root_logger.handlers:
        if isinstance(existing_handler, AuditLogFileHandler):
            # Handler already exists, return it
            std_logger = logging.getLogger(__name__)
            std_logger.info(f"Audit logging already configured, reusing existing handler")
            return existing_handler
    
    # Create new handler
    handler = AuditLogFileHandler(base_dir)
    
    # Add to root logger to capture all logs
    root_logger.addHandler(handler)
    
    # Use standard logger (not structlog) to avoid circular import
    std_logger = logging.getLogger(__name__)
    std_logger.info(f"Audit logging configured with base_dir={base_dir}")
    
    return handler

