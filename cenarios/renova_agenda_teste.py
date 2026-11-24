#!/usr/bin/env python3
"""Acrescenta horarios livres ao banco de TESTE dos cenarios.

Cada execucao dos cenarios reserva horarios; depois de varias rodadas a agenda
de teste se esgota e os cenarios falham por falta de horario (nao por erro do
chatbot). Este script insere horarios livres (8h-12h e 14h-18h, de 30 em 30
min) para todos os medicos nos proximos 10 dias uteis, sem apagar nada: o
historico das rodadas anteriores fica no banco. Horarios ja existentes sao
mantidos (INSERT OR IGNORE).

Recusa-se a rodar no banco real (clinica.db).

Uso (venv ativo, na raiz do projeto):  python cenarios\\renova_agenda_teste.py
"""
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TESTE = RAIZ / "rasa" / "actions" / "clinica_cenarios.db"
DIAS_UTEIS = 10
BLOCOS = [(time(8, 0), time(12, 0)), (time(14, 0), time(18, 0))]
PASSO = timedelta(minutes=30)

if TESTE.name == "clinica.db" or not TESTE.exists():
    sys.exit(f"Banco de teste invalido: {TESTE}")

con = sqlite3.connect(TESTE)
dias, d = [], date.today() + timedelta(days=1)
while len(dias) < DIAS_UTEIS:
    if d.weekday() < 5:
        dias.append(d)
    d += timedelta(days=1)
novos = 0
for (medico_id,) in con.execute("SELECT id FROM medico").fetchall():
    for dia in dias:
        for ini, fim in BLOCOS:
            t = datetime.combine(dia, ini)
            while t + PASSO <= datetime.combine(dia, fim):
                novos += con.execute(
                    "INSERT OR IGNORE INTO horario (medico_id, data, hora_inicio, hora_fim, status) "
                    "VALUES (?, ?, ?, ?, 'disponivel')",
                    (medico_id, dia.isoformat(), t.strftime("%H:%M"), (t + PASSO).strftime("%H:%M")),
                ).rowcount
                t += PASSO
con.commit()
livres = con.execute("SELECT COUNT(*) FROM horario WHERE status = 'disponivel' AND data > ?",
                     (date.today().isoformat(),)).fetchone()[0]
con.close()
print(f"{novos} horarios novos de {dias[0]} a {dias[-1]}; {livres} horarios livres a partir de amanha.")
