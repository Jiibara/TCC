-- =====================================================================
-- Migração: troca a UNIQUE simples de consulta.horario_id por
-- índice único parcial (consultas ativas). Rode UMA vez no banco existente.
-- FAÇA BACKUP DO ARQUIVO .db ANTES.
-- =====================================================================
PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

CREATE TABLE consulta_nova (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id    INTEGER NOT NULL REFERENCES paciente(id),
    horario_id     INTEGER NOT NULL REFERENCES horario(id),
    status         TEXT NOT NULL
                   CHECK (status IN ('agendada', 'cancelada', 'reagendada', 'concluida')),
    criado_em      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO consulta_nova (id, paciente_id, horario_id, status, criado_em, atualizado_em)
SELECT id, paciente_id, horario_id, status, criado_em, atualizado_em FROM consulta;

DROP TABLE consulta;
ALTER TABLE consulta_nova RENAME TO consulta;

CREATE UNIQUE INDEX ux_consulta_horario_ativo
    ON consulta (horario_id) WHERE status IN ('agendada', 'reagendada');
CREATE INDEX ix_consulta_pac ON consulta (paciente_id, status);

COMMIT;
PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;   -- não deve listar nada
