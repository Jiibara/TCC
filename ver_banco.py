"""
Utilitário para visualizar o conteúdo do clinica.db no terminal.

Uso (de dentro de chatbot-clinica\\rasa):
    python ..\\ver_banco.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rasa", "actions", "clinica.db")

TABELAS = [
    "paciente",
    "medico",
    "especialidade",
    "medico_especialidade",
    "convenio",
    "paciente_convenio",
    "horario",
    "consulta",
    "lembrete",
    "consentimento_lgpd",
    "log_interacao",
]


def formatar_linha(row: sqlite3.Row) -> str:
    return " | ".join(f"{k}={row[k]}" for k in row.keys())


def main():
    if not os.path.exists(DB_PATH):
        print(f"Banco não encontrado em: {DB_PATH}")
        print("Rode primeiro o comando que cria o clinica.db a partir do schema.sql.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    for tabela in TABELAS:
        print(f"\n=== {tabela.upper()} ===")
        try:
            linhas = cur.execute(f"SELECT * FROM {tabela}").fetchall()
        except sqlite3.OperationalError as e:
            print(f"  (erro ao ler: {e})")
            continue

        if not linhas:
            print("  (vazio)")
            continue

        for row in linhas:
            print(" ", formatar_linha(row))

    conn.close()


if __name__ == "__main__":
    main()
