"""
Base factory - eliminates duplicated create_from_dict patterns.

This base class provides common patterns used by all domain factories:
- create_from_dict: Validate → Create → Validate

Eliminates ~150 lines of duplicated code across 7+ factory methods.
"""
from typing import Dict, Any, Optional, TypeVar, Type, Callable
from pydantic import BaseModel

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

TModel = TypeVar('TModel', bound=BaseModel)


class BaseFactory:
    """
    Base class for domain factories - handles common creation patterns.
    
    All factories follow similar patterns:
    1. Validate raw data
    2. Create domain model
    3. Validate domain model
    
    This base class eliminates code duplication while maintaining flexibility.
    """
    
    @staticmethod
    def create_from_dict(
        data: Dict[str, Any],
        model_class: Type[TModel],
        validate_raw_data: Optional[Callable[[Dict[str, Any]], bool]] = None,
        validate_model: Optional[Callable[[TModel], bool]] = None,
        factory_name: str = "Factory"
    ) -> Optional[TModel]:
        """
        Generic create_from_dict method - eliminates duplication.
        
        Common pattern:
        1. Validate raw data (optional)
        2. Create domain model (using model's from_dict or constructor)
        3. Validate domain model (optional)
        
        Args:
            data: Raw data dictionary
            model_class: Pydantic model class to create
            validate_raw_data: Optional function to validate raw data before creation
            validate_model: Optional function to validate created model
            factory_name: Name of factory (for logging)
        
        Returns:
            Domain model instance, or None if invalid
        
        Usage:
            # In TradeRequestFactory:
            @staticmethod
            def create_from_dict(data: Dict[str, Any]) -> Optional[TradeRequest]:
                return BaseFactory.create_from_dict(
                    data=data,
                    model_class=TradeRequest,
                    validate_raw_data=TradeRequestValidator.is_valid_trade_request_data,
                    validate_model=TradeRequestValidator.is_valid_domain_trade_request,
                    factory_name="TradeRequestFactory"
                )
        """
        try:
            # Step 1: VALIDATE raw data (optional)
            if validate_raw_data and not validate_raw_data(data):
                logger.warning(f"{factory_name}: Invalid raw data provided")
                return None
            
            # Step 2: CREATE domain model
            # Try from_dict method first (if model has it), otherwise use constructor
            if hasattr(model_class, 'from_dict'):
                model = model_class.from_dict(data)
            else:
                # Use Pydantic's model_validate
                model = model_class.model_validate(data)
            
            if not model:
                logger.warning(f"{factory_name}: Failed to create model from dict")
                return None
            
            # Step 3: VALIDATE domain model (optional)
            if validate_model and not validate_model(model):
                logger.warning(f"{factory_name}: Created model failed validation")
                return None
            
            logger.debug(f"{factory_name}: Created model from dict")
            return model
            
        except Exception as e:
            logger.error(
                f"{factory_name}: Error creating model from dict",
                error=str(e),
                exc_info=True
            )
            return None

