-- Migration 003: pgvector extension + embedding column
-- Enables semantic search on playbook_chunks for RAG.

-- ── Enable pgvector ───────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Add embedding column to playbook_chunks ───────────
-- Uses the pgvector `vector` type. Dimension=3072 for DeepSeek-v4 embeddings;
-- adjust if using OpenAI (1536) or other embedding model.
ALTER TABLE playbook_chunks
ADD COLUMN embedding vector(3072);

-- ── Index for similarity search ───────────────────────
-- IVFFlat is good for >1000 rows; swap to HNSW for massive scale in Phase 2.
CREATE INDEX idx_playbook_chunks_embedding
ON playbook_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- ── Helper function: cosine similarity search ─────────
CREATE OR REPLACE FUNCTION search_playbook_chunks(
    query_embedding vector(3072),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_type TEXT DEFAULT NULL       -- optional: 'objection', 'sequence', 'pricing', 'proof'
)
RETURNS TABLE (
    id UUID,
    playbook_id UUID,
    chunk_type TEXT,
    content TEXT,
    metadata JSONB,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        pc.id,
        pc.playbook_id,
        pc.chunk_type,
        pc.content,
        pc.metadata,
        1 - (pc.embedding <=> query_embedding) AS similarity
    FROM playbook_chunks pc
    WHERE (filter_type IS NULL OR pc.chunk_type = filter_type)
      AND 1 - (pc.embedding <=> query_embedding) > match_threshold
    ORDER BY pc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;
