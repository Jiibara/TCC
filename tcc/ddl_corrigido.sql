-- =====================================================================
-- Script DDL do protótipo (SQLite 3) - Apêndice A
-- Reconstruído a partir do dicionário de dados (Tabelas 9 a 19),
-- já com a correção do índice único parcial em consulta.horario_id.
-- =====================================================================
PRAGMA foreign_keys = ON;

CREATE TABLE convenio (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nome   TEXT    NOT NULL UNIQUE,
    ativo  INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE especialidade (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    nome  TEXT NOT NULL UNIQUE
);

CREATE TABLE medico (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    nome  TEXT NOT NULL
);

CREATE TABLE medico_especialidade (
    medico_id        INTEGER NOT NULL REFERENCES medico(id),
    especialidade_id INTEGER NOT NULL REFERENCES especialidade(id),
    principal        INTEGER NOT NULL DEFAULT 0 CHECK (principal IN (0, 1)),
    PRIMARY KEY (medico_id, especialidade_id)
);

CREATE TABLE paciente (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nome              TEXT NOT NULL,
    telefone          TEXT NOT NULL,
    data_nascimento   TEXT NOT NULL,                       -- 'AAAA-MM-DD'
    criado_em         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    telegram_chat_id  TEXT UNIQUE
);

CREATE TABLE paciente_convenio (
    paciente_id         INTEGER NOT NULL REFERENCES paciente(id),
    convenio_id         INTEGER NOT NULL REFERENCES convenio(id),
    numero_carteirinha  TEXT,
    principal           INTEGER NOT NULL DEFAULT 0 CHECK (principal IN (0, 1)),
    PRIMARY KEY (paciente_id, convenio_id)
);

CREATE TABLE horario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medico_id   INTEGER NOT NULL REFERENCES medico(id),
    data        TEXT NOT NULL,                             -- 'AAAA-MM-DD'
    hora_inicio TEXT NOT NULL,                             -- 'HH:MM'
    hora_fim    TEXT NOT NULL,                             -- 'HH:MM'
    status      TEXT NOT NULL
                CHECK (status IN ('disponivel', 'reservado', 'bloqueado'))
);

CREATE TABLE consulta (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id    INTEGER NOT NULL REFERENCES paciente(id),
    horario_id     INTEGER NOT NULL REFERENCES horario(id),   -- sem UNIQUE simples
    status         TEXT NOT NULL
                   CHECK (status IN ('agendada', 'cancelada', 'reagendada', 'concluida')),
    criado_em      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Regra de integridade: no máximo UMA consulta ativa por horário.
-- Consultas canceladas ou concluídas preservam o histórico e não bloqueiam o horário.
CREATE UNIQUE INDEX ux_consulta_horario_ativo
    ON consulta (horario_id)
    WHERE status IN ('agendada', 'reagendada');

CREATE TABLE lembrete (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    consulta_id       INTEGER NOT NULL REFERENCES consulta(id),
    data_envio        TEXT NOT NULL,
    status_confirmacao TEXT NOT NULL DEFAULT 'pendente'
                      CHECK (status_confirmacao IN ('pendente', 'confirmado', 'ignorado')),
    enviado_em        TEXT
);

CREATE TABLE consentimento_lgpd (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id  INTEGER NOT NULL REFERENCES paciente(id),
    finalidade   TEXT NOT NULL,
    base_legal   TEXT NOT NULL,
    concedido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revogado_em  TEXT
);

CREATE TABLE log_interacao (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id      INTEGER REFERENCES paciente(id),      -- opcional (nullable)
    intent_detectado TEXT NOT NULL,
    confianca        REAL NOT NULL CHECK (confianca BETWEEN 0.0 AND 1.0),
    timestamp        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    canal            TEXT NOT NULL DEFAULT 'chatbot'
);

-- Índices de desempenho
CREATE INDEX ix_horario_busca   ON horario (medico_id, data, status);
CREATE INDEX ix_consulta_pac    ON consulta (paciente_id, status);
CREATE INDEX ix_lembrete_envio  ON lembrete (data_envio, enviado_em);
