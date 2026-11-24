"""
Migração única do clinica.db para o cadastro/consentimento/lembretes.
Pode rodar mais de uma vez (só aplica o que ainda falta).

    python migrar_banco.py

Fica na pasta `rasa` (NÃO dentro de `rasa/actions`). Rode com o `rasa run actions` parado.
"""
import os
import sqlite3

# Banco: variável CLINICA_DB, senão rasa/actions/clinica.db (onde o bot o mantém).
_AQUI = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CLINICA_DB") or os.path.join(_AQUI, "actions", "clinica.db")


def colunas(conn, tabela):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")]


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"Banco não encontrado: {DB_PATH}\nDefina CLINICA_DB com o caminho do clinica.db e rode de novo.")
    print(f"Banco: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        for tabela in ("paciente", "lembrete", "consentimento_lgpd", "consulta"):
            if not colunas(conn, tabela):
                raise SystemExit(f"Tabela '{tabela}' não existe em {DB_PATH}.")

        # paciente.telegram_chat_id: liga o chat do Telegram ao paciente
        if "telegram_chat_id" not in colunas(conn, "paciente"):
            conn.execute("ALTER TABLE paciente ADD COLUMN telegram_chat_id TEXT")
            print("+ paciente.telegram_chat_id")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_paciente_telegram_chat ON paciente(telegram_chat_id)")

        # lembrete.enviado_em: NULL = ainda não enviado
        if "enviado_em" not in colunas(conn, "lembrete"):
            conn.execute("ALTER TABLE lembrete ADD COLUMN enviado_em DATETIME")
            print("+ lembrete.enviado_em")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_lembrete_envio ON lembrete(status_confirmacao, enviado_em, data_envio)"
        )
        conn.commit()
        print("Migração concluída.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()