"""
Popula a tabela `horario` com os próximos dias úteis, em blocos de manhã
e tarde, para cada médico cadastrado. Não duplica horários existentes
(mesmo médico + data + hora).

Uso (rode de qualquer pasta; o caminho é relativo a este arquivo):
    python seed_horarios.py            # completa os próximos dias úteis e
                                       # limpa horários livres já passados
    python seed_horarios.py --reset    # RECRIA o banco a partir do schema.sql
                                       # (apaga consultas!) e gera horários

Rode de novo sempre que for apresentar/testar: os horários são gerados a
partir do dia em que o script roda, então "envelhecem" com o tempo.
Obrigatório rodar com --reset uma vez após atualizar o schema.sql
(o índice parcial de consultas ativas substitui o UNIQUE antigo).
"""

import argparse
import os
import sqlite3
from datetime import date, datetime, timedelta, time

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "rasa", "actions", "clinica.db")

DIAS_UTEIS_A_GERAR = 10          # quantos próximos dias úteis (seg-sex)
DURACAO_CONSULTA_MIN = 30

BLOCOS = [
    (time(8, 0), time(12, 0)),    # manhã
    (time(14, 0), time(18, 0)),   # tarde
]


def achar_schema() -> str:
    for pasta in (BASE, os.path.join(BASE, "rasa"),
                  os.path.join(BASE, "rasa", "actions"),
                  os.path.join(BASE, "database")):
        caminho = os.path.join(pasta, "schema.sql")
        if os.path.exists(caminho):
            return caminho
    raise FileNotFoundError("schema.sql não encontrado (procurei na raiz, rasa/, rasa/actions/ e database/)")


def proximos_dias_uteis(quantidade: int):
    dias = []
    d = date.today() + timedelta(days=1)
    while len(dias) < quantidade:
        if d.weekday() < 5:  # 0=segunda ... 4=sexta
            dias.append(d)
        d += timedelta(days=1)
    return dias


def gerar_horarios_do_dia():
    horarios = []
    for inicio, fim in BLOCOS:
        atual = datetime.combine(date.today(), inicio)
        limite = datetime.combine(date.today(), fim)
        passo = timedelta(minutes=DURACAO_CONSULTA_MIN)
        while atual + passo <= limite:
            horarios.append((atual.time(), (atual + passo).time()))
            atual += passo
    return horarios


def recriar_banco() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    with open(achar_schema(), encoding="utf-8") as f:
        conn.executescript(f.read())
    print("Banco recriado a partir do schema.sql.")
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="recria o banco (apaga consultas)")
    args = ap.parse_args()

    if args.reset:
        conn = recriar_banco()
    else:
        if not os.path.exists(DB_PATH):
            print(f"Banco não encontrado em: {DB_PATH}\nRode com --reset para criá-lo.")
            return
        conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # Limpeza: horários livres de dias passados. Só remove os que NÃO são
    # referenciados por nenhuma consulta (consultas canceladas mantêm o
    # horario_id como histórico e bloqueariam a exclusão).
    removidos = cur.execute(
        "DELETE FROM horario WHERE status = 'disponivel' AND data < ? "
        "AND id NOT IN (SELECT horario_id FROM consulta)",
        (date.today().isoformat(),),
    ).rowcount

    medicos = cur.execute("SELECT id, nome FROM medico").fetchall()
    if not medicos:
        print("Nenhum médico cadastrado — rode com --reset (usa o schema.sql).")
        conn.close()
        return

    dias = proximos_dias_uteis(DIAS_UTEIS_A_GERAR)
    janelas = gerar_horarios_do_dia()

    inseridos = 0
    for medico_id, _nome in medicos:
        for dia in dias:
            for hora_inicio, hora_fim in janelas:
                cur.execute(
                    "INSERT OR IGNORE INTO horario (medico_id, data, hora_inicio, hora_fim, status) "
                    "VALUES (?, ?, ?, ?, 'disponivel')",
                    (medico_id, dia.isoformat(), hora_inicio.strftime("%H:%M"), hora_fim.strftime("%H:%M")),
                )
                inseridos += cur.rowcount

    conn.commit()
    conn.close()
    print(f"{inseridos} novos horários inseridos para {len(medicos)} médico(s), "
          f"em {len(dias)} dias úteis. {removidos} horários passados removidos.")


if __name__ == "__main__":
    main()