"""Development playbook store with keyword matching only.

Vector retrieval and a production Supabase store have not been implemented.
"""

import uuid

from backend.app.db.supabase import DB_PATH, _get_sqlite

# ── Store interface ────────────────────────────────────


class PlaybookStore:
    """Local development playbook chunks with keyword search."""

    def __init__(self):
        self.db_path = DB_PATH

    def add_chunk(
        self,
        playbook_id: str,
        chunk_type: str,
        content: str,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
    ) -> str:
        """Add text; reject embeddings rather than silently discarding them."""
        import json

        if embedding is not None:
            raise NotImplementedError("Vector embedding storage is not implemented")
        conn = _get_sqlite()
        try:
            chunk_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO playbook_chunks (id, playbook_id, chunk_type, content, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    chunk_id,
                    playbook_id,
                    chunk_type,
                    content,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()
            return chunk_id
        finally:
            conn.close()

    def search(
        self,
        query: str,
        top_k: int = 5,
        chunk_type: str | None = None,
    ) -> list[dict]:
        """Keyword search (SQLite fallback). Upgrade to pgvector cosine when available."""
        conn = _get_sqlite()
        try:
            params = []

            # Simple keyword match: LIKE on content
            terms = query.split()
            clauses = []
            if terms:
                clauses.append(" AND ".join(["content LIKE ?" for _ in terms]))
                params.extend([f"%{t}%" for t in terms])

            if chunk_type:
                clauses.append("chunk_type = ?")
                params.append(chunk_type)

            where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

            rows = conn.execute(
                f"""SELECT id, playbook_id, chunk_type, content, metadata
                    FROM playbook_chunks
                    {where_sql}
                    LIMIT ?""",
                params + [top_k],
            ).fetchall()

            return [
                {
                    "id": r["id"],
                    "playbook_id": r["playbook_id"],
                    "chunk_type": r["chunk_type"],
                    "content": r["content"],
                    "metadata": r["metadata"],
                    "match_type": "keyword",
                }
                for r in rows
            ]
        finally:
            conn.close()

    def search_by_type(
        self,
        chunk_type: str,
        query: str = "",
        top_k: int = 5,
    ) -> list[dict]:
        """Search within a specific chunk type."""
        return self.search(query=query, top_k=top_k, chunk_type=chunk_type)

    def get_context(
        self,
        query: str,
        top_k: int = 5,
    ) -> str:
        """Return concatenated context string for RAG prompt injection."""
        results = self.search(query, top_k=top_k)
        if not results:
            return ""
        lines = []
        for r in results:
            lines.append(f"[{r['chunk_type']}] {r['content']}")
        return "\n\n".join(lines)


# ── Singleton ──────────────────────────────────────────
_store: PlaybookStore | None = None


def get_playbook_store() -> PlaybookStore:
    global _store
    if _store is None:
        _store = PlaybookStore()
    return _store
