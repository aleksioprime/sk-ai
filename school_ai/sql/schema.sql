-- Схема БД school_ai.
-- Скрипт идемпотентный: его можно запускать повторно, в том числе
-- поверх первой версии схемы (она будет дополнена недостающими полями).
-- {EMBED_DIMENSIONS} подставляется из настроек (по умолчанию 1024).

-- -------------------------------------------------------------------
-- Документы (один файл = одна запись)
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    filename      TEXT NOT NULL,
    title         TEXT,
    document_type TEXT,
    revision      TEXT,
    file_path     TEXT NOT NULL,
    file_hash     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- collection: 'documents' (нормативная база) или 'reports' (примеры отчётов)
ALTER TABLE documents ADD COLUMN IF NOT EXISTS collection TEXT NOT NULL DEFAULT 'documents';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_version INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS pages INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ;

-- Один и тот же файл может лежать в двух папках — уникален путь, а не хэш.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_hash_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_file_path ON documents(file_path);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);

-- -------------------------------------------------------------------
-- Структура документа: главы, разделы, пункты, подпункты
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_sections (
    id             BIGSERIAL PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id      BIGINT REFERENCES document_sections(id) ON DELETE CASCADE,
    section_number TEXT,
    title          TEXT,
    level          INTEGER NOT NULL DEFAULT 1,
    position       INTEGER NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- kind: numbered (пункт с номером), named (Глава/Раздел/Статья),
--       heading (заголовок без номера), preamble (текст до первого раздела)
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS kind TEXT;
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS heading_path TEXT;
ALTER TABLE document_sections ADD COLUMN IF NOT EXISTS page INTEGER;

CREATE INDEX IF NOT EXISTS idx_sections_document ON document_sections(document_id);
CREATE INDEX IF NOT EXISTS idx_sections_parent ON document_sections(parent_id);
CREATE INDEX IF NOT EXISTS idx_sections_doc_number ON document_sections(document_id, section_number);
CREATE INDEX IF NOT EXISTS idx_sections_title_fts_ru
    ON document_sections USING GIN (to_tsvector('russian', COALESCE(title, '')));

-- -------------------------------------------------------------------
-- Фрагменты для поиска (то, что сравнивается с вопросом)
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_chunks (
    id             BIGSERIAL PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id     BIGINT REFERENCES document_sections(id) ON DELETE SET NULL,
    chunk_index    INTEGER NOT NULL,
    section_number TEXT,
    text           TEXT NOT NULL,
    embedding      vector({EMBED_DIMENSIONS}),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS heading_path TEXT;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page INTEGER;

CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON document_chunks(section_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section_number ON document_chunks(section_number);
CREATE INDEX IF NOT EXISTS idx_chunks_fts_ru
    ON document_chunks USING GIN (to_tsvector('russian', text));
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- -------------------------------------------------------------------
-- Журнал запросов (audit trail): что спросили, что ответили, на что сослались
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_queries (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    question    TEXT NOT NULL,
    collection  TEXT,
    chat_model  TEXT,
    embed_model TEXT,
    answer      TEXT,
    result      JSONB,
    timings     JSONB
);

-- кто спросил (email/имя пользователя из Open WebUI)
ALTER TABLE rag_queries ADD COLUMN IF NOT EXISTS user_name TEXT;

-- -------------------------------------------------------------------
-- Статус индексации каждого файла (для страницы «Документы»)
-- status: queued | indexing | indexed | empty | error
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS index_status (
    file_path  TEXT PRIMARY KEY,           -- путь относительно папки данных
    status     TEXT NOT NULL,
    message    TEXT,
    warnings   JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
