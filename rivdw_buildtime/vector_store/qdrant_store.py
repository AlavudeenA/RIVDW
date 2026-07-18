"""All Qdrant vector store operations: save, search, update, reset."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.models import Distance, VectorParams

from config.settings import get_settings
from models.metadata_entry import MetadataEntry
from vector_store.embedding import embed_single

logger = logging.getLogger(__name__)


def _point_id(entry_id: str) -> int:
    """Convert a string entry ID to a stable Qdrant integer point ID.

    Uses SHA-256 so the result is identical across every Python process.
    Python's built-in hash() is randomised per-process and must NOT be used here.
    """
    return int(hashlib.sha256(entry_id.encode()).hexdigest()[:15], 16)


# BAAI/bge-small-en-v1.5 produces 384-dimensional vectors
_VECTOR_SIZE = 384


class QdrantStore:
    """Wraps a local Qdrant instance and exposes save, search, update, and reset operations."""

    def __init__(self) -> None:
        settings = get_settings()
        qdrant_path = settings.qdrant_path
        self._collection_name = settings.qdrant_collection

        Path(qdrant_path).mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=qdrant_path)
        self._ensure_collection_exists()

    def _ensure_collection_exists(self) -> None:
        """Create the Qdrant collection if it does not already exist."""
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection_name not in existing:
            self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s", self._collection_name)

    def _embed(self, text: str) -> List[float]:
        """Generate an embedding vector for the given text using the shared embedding module."""
        return embed_single(text)

    def save_entry(self, entry: MetadataEntry) -> None:
        """Upsert one MetadataEntry into the vector store, embedding a context-prefixed description
        so the vector captures which database/table/column it belongs to, not just the prose."""
        location = entry.table_name if entry.is_table_entry() else f"{entry.table_name}.{entry.column_name}"
        body = entry.description or location
        parts = [f"{entry.source_db} > {location}: {body}"]
        if entry.human_notes:
            parts.append(entry.human_notes)
        embed_text = " ".join(parts)
        vector = self._embed(embed_text)

        payload = {
            "id": entry.id,
            "source_db": entry.source_db,
            "db_type": entry.db_type,
            "domain_tag": entry.domain_tag,
            "schema_name": entry.schema_name,
            "table_name": entry.table_name,
            "column_name": entry.column_name,
            "data_type": entry.data_type,
            "nullable": entry.nullable,
            "description": entry.description,
            "business_terms": entry.business_terms,
            "related_tables": entry.related_tables,
            "business_process": entry.business_process,
            "guardian_status": entry.guardian_status,
            "human_verified": entry.human_verified,
            "human_notes": entry.human_notes,
            "last_updated": entry.last_updated.isoformat(),
        }

        point_id = _point_id(entry.id)

        self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def save_entries(self, entries: List[MetadataEntry]) -> int:
        """Save a list of entries; returns the count of successfully saved entries."""
        saved_count = 0
        for entry in entries:
            try:
                self.save_entry(entry)
                saved_count += 1
            except Exception as error:
                logger.error("Failed to save entry %s to vector store: %s", entry.id, error)
        logger.info("Saved %d entries to Qdrant collection '%s'", saved_count, self._collection_name)
        return saved_count

    def search(
        self,
        query_text: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search the vector store by semantic similarity to query_text."""
        query_vector = self._embed(query_text)

        qdrant_filter = _build_filter(filters) if filters else None

        response = self._client.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            query_filter=qdrant_filter,
            limit=limit,
            with_payload=True,
        )

        return [{"score": hit.score, **hit.payload} for hit in response.points]

    def get_all_entries(
        self,
        filters: Optional[Dict[str, str]] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch entries from the store without a search query, optionally filtered."""
        qdrant_filter = _build_filter(filters) if filters else None

        results, _ = self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=qdrant_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
        )

        return [point.payload for point in results]

    def update_entry_fields(self, entry_id: str, fields: Dict[str, Any]) -> bool:
        """
        Update specific payload fields on the entry matching entry_id.
        Returns True if the entry was found and updated.
        """
        point_id = _point_id(entry_id)

        fields["last_updated"] = datetime.now(timezone.utc).isoformat()

        try:
            self._client.set_payload(
                collection_name=self._collection_name,
                payload=fields,
                points=[point_id],
            )
            return True
        except Exception as error:
            logger.error("Failed to update entry %s: %s", entry_id, error)
            return False

    def get_entry_by_id(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one entry by its string entry_id. Returns None if not found."""
        point_id = _point_id(entry_id)
        try:
            results = self._client.retrieve(
                collection_name=self._collection_name,
                ids=[point_id],
                with_payload=True,
            )
            if results:
                return results[0].payload
            return None
        except Exception as error:
            logger.error("Failed to retrieve entry %s: %s", entry_id, error)
            return None

    def reset(self) -> None:
        """Delete all stored metadata by dropping and recreating the collection."""
        self._client.delete_collection(collection_name=self._collection_name)
        self._ensure_collection_exists()
        logger.info("Vector store reset: collection '%s' cleared", self._collection_name)

    def count(self) -> int:
        """Return the total number of entries in the collection."""
        info = self._client.get_collection(collection_name=self._collection_name)
        return info.points_count or 0


def _build_filter(filters: Dict[str, Any]) -> qdrant_models.Filter:
    """Convert a plain dict of field:value pairs into a Qdrant Filter object."""
    conditions = []
    for field, value in filters.items():
        if value and value != "All":
            conditions.append(
                qdrant_models.FieldCondition(
                    key=field,
                    match=qdrant_models.MatchValue(value=value),
                )
            )
    return qdrant_models.Filter(must=conditions) if conditions else None


_store_instance: Optional[QdrantStore] = None


def get_store() -> QdrantStore:
    """Return the singleton QdrantStore, initialising it on first call."""
    global _store_instance
    if _store_instance is None:
        _store_instance = QdrantStore()
    return _store_instance
