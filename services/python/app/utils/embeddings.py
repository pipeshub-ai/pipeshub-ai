from langchain_huggingface import HuggingFaceEmbeddings

from app.config.utils.named_constants.ai_models_named_constants import DEFAULT_EMBEDDING_MODEL


async def get_default_embedding_model():
    try:
        model_name = DEFAULT_EMBEDDING_MODEL
        encode_kwargs = {'normalize_embeddings': True}
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": "cpu"},
            encode_kwargs=encode_kwargs,
        )
    except Exception as e:
        raise e
