"""
Model Manager for LangGraph Agent System

This module provides AI model management and interaction capabilities for agents.
"""

import logging
from typing import Dict, Optional, Any
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.llms import Ollama

from app.models.agents import ModelConfig, ModelProvider, ModelType

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages AI model interactions"""
    
    def __init__(self):
        self.models: Dict[str, BaseLanguageModel] = {}
        self.model_configs: Dict[str, ModelConfig] = {}
        self.primary_model: Optional[str] = None
    
    def register_model(self, model_name: str, model_config: ModelConfig):
        """Register a model configuration"""
        try:
            # Create model based on provider
            if model_config.provider == ModelProvider.OPENAI:
                model = ChatOpenAI(
                    model=model_config.name,
                    temperature=model_config.config.get("temperature", 0.7),
                    max_tokens=model_config.config.get("max_tokens", 1000),
                    api_key=model_config.config.get("api_key"),
                    base_url=model_config.config.get("base_url")
                )
            elif model_config.provider == ModelProvider.ANTHROPIC:
                model = ChatAnthropic(
                    model=model_config.name,
                    temperature=model_config.config.get("temperature", 0.7),
                    max_tokens=model_config.config.get("max_tokens", 1000),
                    api_key=model_config.config.get("api_key")
                )
            elif model_config.provider == ModelProvider.GOOGLE:
                model = ChatGoogleGenerativeAI(
                    model=model_config.name,
                    temperature=model_config.config.get("temperature", 0.7),
                    google_api_key=model_config.config.get("api_key")
                )
            elif model_config.provider == ModelProvider.OLLAMA:
                model = Ollama(
                    model=model_config.name,
                    temperature=model_config.config.get("temperature", 0.7),
                    base_url=model_config.config.get("base_url", "http://localhost:11434")
                )
            else:
                logger.warning(f"Unsupported model provider: {model_config.provider}")
                return
            
            self.models[model_name] = model
            self.model_configs[model_name] = model_config
            
            # Set as primary if it's the first model
            if not self.primary_model:
                self.primary_model = model_name
            
            logger.info(f"Registered model: {model_name} ({model_config.provider})")
            
        except Exception as e:
            logger.error(f"Failed to register model {model_name}: {str(e)}")
    
    def get_model(self, model_name: str) -> Optional[BaseLanguageModel]:
        """Get model by name"""
        return self.models.get(model_name)
    
    def get_primary_model(self) -> Optional[BaseLanguageModel]:
        """Get the primary model"""
        if self.primary_model:
            return self.models.get(self.primary_model)
        return next(iter(self.models.values()), None) if self.models else None
    
    def set_primary_model(self, model_name: str) -> bool:
        """Set the primary model"""
        if model_name in self.models:
            self.primary_model = model_name
            logger.info(f"Set primary model to: {model_name}")
            return True
        else:
            logger.warning(f"Model {model_name} not found, cannot set as primary")
            return False
    
    def list_models(self) -> Dict[str, Dict[str, Any]]:
        """List all registered models with their configurations"""
        return {
            name: {
                "provider": config.provider.value,
                "model_type": config.model_type.value,
                "name": config.name,
                "is_primary": name == self.primary_model
            }
            for name, config in self.model_configs.items()
        }
    
    def remove_model(self, model_name: str) -> bool:
        """Remove a model from registry"""
        if model_name in self.models:
            del self.models[model_name]
            del self.model_configs[model_name]
            
            # Update primary model if needed
            if self.primary_model == model_name:
                self.primary_model = next(iter(self.models.keys()), None)
            
            logger.info(f"Removed model: {model_name}")
            return True
        return False
    
    def clear_models(self):
        """Clear all models"""
        self.models.clear()
        self.model_configs.clear()
        self.primary_model = None
        logger.info("Cleared all models")
    
    def get_model_config(self, model_name: str) -> Optional[ModelConfig]:
        """Get model configuration"""
        return self.model_configs.get(model_name)
    
    def update_model_config(self, model_name: str, new_config: ModelConfig) -> bool:
        """Update model configuration"""
        if model_name in self.models:
            # Remove old model
            del self.models[model_name]
            
            # Register with new config
            self.register_model(model_name, new_config)
            return True
        return False
    
    def test_model(self, model_name: str, test_prompt: str = "Hello, how are you?") -> Dict[str, Any]:
        """Test a model with a simple prompt"""
        model = self.get_model(model_name)
        if not model:
            return {"success": False, "error": "Model not found"}
        
        try:
            response = model.invoke(test_prompt)
            return {
                "success": True,
                "response": response.content if hasattr(response, 'content') else str(response),
                "model_name": model_name
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "model_name": model_name
            }
    
    def get_model_capabilities(self, model_name: str) -> Dict[str, Any]:
        """Get model capabilities and limitations"""
        config = self.get_model_config(model_name)
        if not config:
            return {}
        
        capabilities = {
            "provider": config.provider.value,
            "model_type": config.model_type.value,
            "name": config.name,
            "max_tokens": config.config.get("max_tokens", "unknown"),
            "temperature": config.config.get("temperature", "unknown"),
            "supports_tools": config.model_type in [ModelType.CHAT, ModelType.FUNCTION_CALLING],
            "supports_vision": config.model_type == ModelType.VISION,
            "supports_streaming": True,  # Most modern models support streaming
        }
        
        return capabilities


# Default model configurations
def create_default_model_configs() -> Dict[str, ModelConfig]:
    """Create default model configurations"""
    configs = {}
    
    # OpenAI models
    configs["gpt-4"] = ModelConfig(
        name="gpt-4",
        provider=ModelProvider.OPENAI,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "max_tokens": 4000,
            "api_key": None  # Will be set from environment
        }
    )
    
    configs["gpt-3.5-turbo"] = ModelConfig(
        name="gpt-3.5-turbo",
        provider=ModelProvider.OPENAI,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "max_tokens": 4000,
            "api_key": None
        }
    )
    
    # Anthropic models
    configs["claude-3-opus"] = ModelConfig(
        name="claude-3-opus-20240229",
        provider=ModelProvider.ANTHROPIC,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "max_tokens": 4000,
            "api_key": None
        }
    )
    
    configs["claude-3-sonnet"] = ModelConfig(
        name="claude-3-sonnet-20240229",
        provider=ModelProvider.ANTHROPIC,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "max_tokens": 4000,
            "api_key": None
        }
    )
    
    # Google models
    configs["gemini-pro"] = ModelConfig(
        name="gemini-pro",
        provider=ModelProvider.GOOGLE,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "api_key": None
        }
    )
    
    # Ollama models (local)
    configs["llama2"] = ModelConfig(
        name="llama2",
        provider=ModelProvider.OLLAMA,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "base_url": "http://localhost:11434"
        }
    )
    
    configs["mistral"] = ModelConfig(
        name="mistral",
        provider=ModelProvider.OLLAMA,
        model_type=ModelType.CHAT,
        config={
            "temperature": 0.7,
            "base_url": "http://localhost:11434"
        }
    )
    
    return configs


def initialize_default_models(manager: ModelManager):
    """Initialize manager with default models"""
    default_configs = create_default_model_configs()
    
    for name, config in default_configs.items():
        manager.register_model(name, config)
    
    logger.info("Initialized default models") 