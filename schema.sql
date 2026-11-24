-- ============================================================
-- Chatbot de Agendamento Clínico — Esquema Físico (DDL)
-- Baseado no Modelo Lógico (seção 4.4.2) e no Dicionário de
-- Dados (seção 4.4.3) do Plano de Projeto (TCC).
-- Compatível com SQLite (ajustar tipos se migrar para
-- PostgreSQL: SERIAL no lugar de INTEGER PK AUTOINCREMENT etc.)
-- ============================================================

PRAGMA foreign_keys = ON;

-- 1) convenio -------------------------------------------------
CREATE TABLE convenio (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome    TEXT NOT NULL UNIQUE,
    ativo   BOOLEAN NOT NULL DEFAULT 1
);

-- 2) especialidade --------------------------------------------
CREATE TABLE especialidade (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome    TEXT NOT NULL UNIQUE
);

-- 3) medico ------------------------------------------------------
CREATE TABLE medico (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome    TEXT NOT NULL
);

-- 4) medico_especialidade (entidade associativa N:N) ----------
CREATE TABLE medico_especialidade (
    medico_id        INTEGER NOT NULL,
    especialidade_id INTEGER NOT NULL,
    principal         BOOLEAN NOT NULL DEFAULT 0,
    PRIMARY KEY (medico_id, especialidade_id),
    FOREIGN KEY (medico_id) REFERENCES medico(id),
    FOREIGN KEY (especialidade_id) REFERENCES especialidade(id)
);

-- 5) paciente ----------------------------------------------------
CREATE TABLE paciente (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nome             TEXT NOT NULL,
    telefone         TEXT NOT NULL,
    data_nascimento  DATE NOT NULL,
    criado_em        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    telegram_chat_id TEXT  -- chat do Telegram do paciente (único: ver índice abaixo)
);

-- 6) paciente_convenio (entidade associativa N:N) --------------
CREATE TABLE paciente_convenio (
    paciente_id        INTEGER NOT NULL,
    convenio_id         INTEGER NOT NULL,
    numero_carteirinha  TEXT,
    principal            BOOLEAN NOT NULL DEFAULT 0,
    PRIMARY KEY (paciente_id, convenio_id),
    FOREIGN KEY (paciente_id) REFERENCES paciente(id),
    FOREIGN KEY (convenio_id) REFERENCES convenio(id)
);

-- 7) horario  (recurso Slot — FHIR) -----------------------------
CREATE TABLE horario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medico_id   INTEGER NOT NULL,
    data        DATE NOT NULL,
    hora_inicio TIME NOT NULL,
    hora_fim    TIME NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('disponivel','reservado','bloqueado')),
    FOREIGN KEY (medico_id) REFERENCES medico(id),
    UNIQUE (medico_id, data, hora_inicio)
);

-- 8) consulta  (recurso Appointment — FHIR) ----------------------
CREATE TABLE consulta (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id   INTEGER NOT NULL,
    horario_id    INTEGER NOT NULL,  -- unicidade só para consultas ATIVAS: ver índice parcial abaixo
    status        TEXT NOT NULL CHECK (status IN ('agendada','cancelada','reagendada','concluida')),
    criado_em     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paciente_id) REFERENCES paciente(id),
    FOREIGN KEY (horario_id) REFERENCES horario(id)
);

-- 9) lembrete ------------------------------------------------------
CREATE TABLE lembrete (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    consulta_id         INTEGER NOT NULL,
    data_envio          DATETIME NOT NULL,
    status_confirmacao  TEXT NOT NULL CHECK (status_confirmacao IN ('pendente','confirmado','ignorado')) DEFAULT 'pendente',
    enviado_em          DATETIME,  -- NULL = lembrete ainda não enviado
    FOREIGN KEY (consulta_id) REFERENCES consulta(id)
);

-- 10) consentimento_lgpd -----------------------------------------
CREATE TABLE consentimento_lgpd (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id   INTEGER NOT NULL,
    finalidade    TEXT NOT NULL,
    base_legal    TEXT NOT NULL,
    concedido_em  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revogado_em   DATETIME,
    FOREIGN KEY (paciente_id) REFERENCES paciente(id)
);

-- 11) log_interacao -------------------------------------------------
CREATE TABLE log_interacao (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id        INTEGER,  -- nullable: msg pode chegar antes da identificação
    intent_detectado   TEXT NOT NULL,
    confianca          REAL NOT NULL,
    timestamp          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    canal              TEXT NOT NULL DEFAULT 'chatbot',
    FOREIGN KEY (paciente_id) REFERENCES paciente(id)
);

-- 12) handoff (fila de atendimento humano) --------------------------
-- Fonte de verdade para "esta conversa está com um humano?".
-- sender_id é o ID do remetente no canal (no Telegram, o chat_id).
CREATE TABLE handoff (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id  TEXT NOT NULL,
    motivo     TEXT,
    status     TEXT NOT NULL DEFAULT 'aguardando'
               CHECK (status IN ('aguardando','em_atendimento','encerrado')),
    criado_em  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Índices de desempenho (consultas mais frequentes do chatbot)
-- ============================================================
-- Um horário só pode ter UMA consulta ativa; consultas canceladas
-- mantêm o horario_id (histórico) sem bloquear nova reserva.
CREATE UNIQUE INDEX ux_consulta_horario_ativa
    ON consulta (horario_id) WHERE status IN ('agendada','reagendada');
CREATE INDEX idx_handoff_sender_status ON handoff (sender_id, status);
CREATE INDEX idx_horario_medico_data_status ON horario (medico_id, data, status);
CREATE INDEX idx_consulta_paciente ON consulta (paciente_id);
CREATE INDEX idx_log_interacao_paciente ON log_interacao (paciente_id);
-- Um chat do Telegram corresponde a no máximo um paciente.
CREATE UNIQUE INDEX ux_paciente_telegram_chat ON paciente (telegram_chat_id);
-- Busca de lembretes pendentes pelo serviço de lembretes.
CREATE INDEX ix_lembrete_envio ON lembrete (status_confirmacao, enviado_em, data_envio);

-- ============================================================
-- Dados de exemplo (seed) — SOMENTE dados sintéticos/fictícios,
-- conforme seção 3.2: nenhum dado real de paciente é utilizado.
-- ============================================================
INSERT INTO especialidade (nome) VALUES ('Clínica Geral'), ('Cardiologia'), ('Endocrinologia'), ('Pediatria');
INSERT INTO convenio (nome) VALUES ('Unimed'), ('Bradesco Saúde'), ('Particular');

INSERT INTO medico (nome) VALUES ('Dra. Ana Ferreira'), ('Dr. Bruno Alves');
INSERT INTO medico_especialidade (medico_id, especialidade_id, principal) VALUES
    (1, 1, 1), (1, 3, 0),   -- Dra. Ana: Clínica Geral (principal) + Endocrinologia
    (2, 2, 1);              -- Dr. Bruno: Cardiologia (principal)

-- Paciente sintético de teste (id = 1), usado pelo protótipo enquanto
-- não há um fluxo real de identificação/cadastro (RF10).
INSERT INTO paciente (nome, telefone, data_nascimento) VALUES
    ('Paciente Teste', '45999990000', '1990-01-01');

-- Horários NÃO são inseridos aqui: rode `python seed_horarios.py` para gerar
-- horários relativos à data atual (evita ofertar datas já passadas).