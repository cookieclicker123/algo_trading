"""
State management for news polling service.
Handles persistence of polling state across restarts.
"""
import os
from dataclasses import dataclass
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PollingState:
    """Immutable polling state."""
    last_seen_article_id: int
    consecutive_errors: int = 0
    backoff_delay: float = 1.0
    
    def increment_errors(self) -> 'PollingState':
        """Return new state with incremented error count."""
        return PollingState(
            last_seen_article_id=self.last_seen_article_id,
            consecutive_errors=self.consecutive_errors + 1,
            backoff_delay=self.backoff_delay
        )
    
    def reset_errors(self) -> 'PollingState':
        """Return new state with reset error count."""
        return PollingState(
            last_seen_article_id=self.last_seen_article_id,
            consecutive_errors=0,
            backoff_delay=self.backoff_delay
        )
    
    def update_last_seen_id(self, article_id: int) -> 'PollingState':
        """Return new state with updated last seen ID."""
        return PollingState(
            last_seen_article_id=article_id,
            consecutive_errors=self.consecutive_errors,
            backoff_delay=self.backoff_delay
        )
    
    def increase_backoff(self) -> 'PollingState':
        """Return new state with increased backoff delay."""
        new_backoff = min(self.backoff_delay * 2, 60.0)  # Max 60 seconds
        return PollingState(
            last_seen_article_id=self.last_seen_article_id,
            consecutive_errors=self.consecutive_errors,
            backoff_delay=new_backoff
        )


class PollingStateManager:
    """
    Manages polling state persistence.
    Separates state management from polling logic.
    """
    
    def __init__(self, state_file: str = "tmp/last_seen_id.txt"):
        """
        Initialize state manager.
        
        Args:
            state_file: Path to state persistence file
        """
        self.state_file = state_file
        self.state = self._load_state()
        
        logger.info(
            "PollingStateManager initialized",
            last_seen_id=self.state.last_seen_article_id,
            consecutive_errors=self.state.consecutive_errors,
            backoff_delay=self.state.backoff_delay
        )
    
    def _load_state(self) -> PollingState:
        """Load state from file or return default."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        return PollingState(last_seen_article_id=int(content))
            
            logger.info("No existing state file found, starting fresh")
            return PollingState(last_seen_article_id=0)
            
        except Exception as e:
            logger.warning("Failed to load state, using defaults", error=str(e))
            return PollingState(last_seen_article_id=0)
    
    def save_state(self, state: PollingState) -> None:
        """Save state to file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            # Save only the last seen ID (backward compatibility)
            with open(self.state_file, "w") as f:
                f.write(str(state.last_seen_article_id))
            
            logger.debug("State saved", last_seen_id=state.last_seen_article_id)
            
        except Exception as e:
            logger.error("Failed to save state", error=str(e))
    
    def get_state(self) -> PollingState:
        """Get current state."""
        return self.state
    
    def update_state(self, new_state: PollingState) -> None:
        """Update state and persist it."""
        self.state = new_state
        self.save_state(new_state)
    
    def update_last_seen_id(self, article_id: int) -> None:
        """Update last seen article ID."""
        new_state = self.state.update_last_seen_id(article_id)
        self.update_state(new_state)
    
    def increment_errors(self) -> None:
        """Increment error count."""
        new_state = self.state.increment_errors()
        self.update_state(new_state)
    
    def reset_errors(self) -> None:
        """Reset error count."""
        new_state = self.state.reset_errors()
        self.update_state(new_state)
    
    def increase_backoff(self) -> None:
        """Increase backoff delay."""
        new_state = self.state.increase_backoff()
        self.update_state(new_state)
