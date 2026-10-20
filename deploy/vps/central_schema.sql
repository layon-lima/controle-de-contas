-- CONTAS 3: tabelas centrais preparadas SEM copiar ou apagar dados.
-- O plano de migracao preservara IDs, pagamentos e campos originais como JSONB.
CREATE TABLE IF NOT EXISTS central_meta (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS contas (
    id BIGINT PRIMARY KEY CHECK (id > 0),
    record JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pagamentos (
    id BIGINT PRIMARY KEY CHECK (id > 0),
    conta_id BIGINT NOT NULL REFERENCES contas(id) ON DELETE RESTRICT,
    record JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pagamentos_conta ON pagamentos (conta_id);
