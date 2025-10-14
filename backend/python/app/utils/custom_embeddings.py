from langchain_voyageai import VoyageAIEmbeddings
from typing import List

class CustomVoyageAIEmbeddings(VoyageAIEmbeddings):
    """Custom VoyageAI embeddings that bypasses model validation."""
    
    def __init__(self, model: str, api_key: str, **kwargs):
        # Store the custom model name
        self.custom_model = model
        
        # Initialize parent class with a supported model first (if validation happens)
        # Then override the model attribute
        try:
            super().__init__(model=model, api_key=api_key, **kwargs)
        except ValueError as e:
            # If model validation fails, use a dummy supported model for init
            # then override with your custom model
            if "not supported" in str(e).lower():
                # Initialize with a known supported model
                super().__init__(model="voyage-2", api_key=api_key, **kwargs)
                # Override the model attribute
                self.model = model
            else:
                raise
    
    # Optional: Override the validation method if it exists
    @property
    def model(self):
        return self._model
    
    @model.setter
    def model(self, value: str):
        # Skip validation and set directly
        self._model = value