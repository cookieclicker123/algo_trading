"""
Translation service for converting English financial news to Chinese.
"""
import json
import structlog
from typing import Dict, Any
from groq import Groq

from ..config.settings import get_classification_config

logger = structlog.get_logger(__name__)


class TranslationService:
    """
    Service for translating English financial news to Chinese using Groq.
    """
    
    def __init__(self, api_key: str, enabled: bool = True):
        """
        Initialize the translation service.
        
        Args:
            api_key: Groq API key
            enabled: Whether translation is enabled
        """
        self.enabled = enabled
        if not enabled:
            logger.info("Translation service disabled")
            return
            
        try:
            self.client = Groq(api_key=api_key)
            logger.info("Translation service initialized")
        except Exception as e:
            logger.error("Failed to initialize translation service", error=str(e))
            self.enabled = False
            return
    
    async def translate_to_chinese(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translate English message data to Chinese.
        
        Args:
            message_data: The message data to translate
            
        Returns:
            Translated message data in Chinese
        """
        if not self.enabled:
            logger.warning("Translation service disabled, returning original data")
            return message_data
            
        try:
            # Load the translation prompt
            prompt_path = "prompts/translation_prompt.txt"
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
            
            # Prepare the message for translation
            message_json = json.dumps(message_data, ensure_ascii=False, indent=2)
            
            # Call Groq for translation
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Translate this English financial news JSON to fluent Chinese:\n\n{message_json}"}
                ],
                temperature=0.1,
                max_tokens=1000,
                timeout=30.0
            )
            
            # Parse the translated response
            translated_text = response.choices[0].message.content.strip()
            
            # Parse the JSON response
            translated_data = json.loads(translated_text)
            
            logger.info(
                "Message translated to Chinese",
                original_keys=list(message_data.keys()),
                translated_keys=list(translated_data.keys())
            )
            
            return translated_data
            
        except json.JSONDecodeError as e:
            logger.error("Failed to parse translated JSON", error=str(e), response=translated_text)
            return message_data
        except Exception as e:
            logger.error("Translation failed", error=str(e))
            return message_data


def get_translation_service() -> TranslationService:
    """Get translation service instance with configuration."""
    config = get_classification_config()
    return TranslationService(
        api_key=config["api_key"],
        enabled=True  # Always enabled since we only call it for confirmed good news
    )
