"""
BGE-M3 embedding wrapper.

Uses FlagEmbedding's BGEM3FlagModel to produce dense vectors that are
stored in / queried against Qdrant. The model is loaded lazily (once)
since it is fairly large.
"""
import logging
from typing import List

from config import EmbeddingSettings

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, settings: EmbeddingSettings):
        self.settings = settings
        self._model = None  # lazy-loaded

    def _load_model(self):
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel  # imported lazily - heavy dependency

            logger.info("Loading embedding model %s ...", self.settings.model_name)
            self._model = BGEM3FlagModel(self.settings.model_name, use_fp16=self.settings.use_fp16)
        return self._model

    def encode(self, texts: List[str]) -> List[List[float]]:
        """Returns dense vectors (list of floats) for each input text."""
        if not texts:
            return []
        model = self._load_model()
        output = model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        dense = output["dense_vecs"]
        # dense is a numpy array (N, dim); convert to plain python lists for Qdrant
        return [vec.tolist() for vec in dense]

    def encode_one(self, text: str) -> List[float]:
        return self.encode([text])[0]
