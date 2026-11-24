#!/usr/bin/env python3
"""Prepara um banco de TESTE limpo para a avaliacao de usabilidade (SUS).

- Copia o banco real (somente leitura) para rasa\\actions\\clinica_sus.db.
- Na copia, apaga TODOS os dados de pacientes (cadastros, consultas,
  consentimentos, lembretes, logs e chamados de atendimento) e libera os
  horarios que estavam reservados. Ficam so os dados da clinica: medicos,
  especialidades, convenios e horarios.
- Garante horarios livres (8h-12h e 14h-18h, de 30 em 30 min) para todos os
  medicos nos proximos 10 dias uteis a partir de hoje.

Rode de novo no dia da aplicacao para ter um banco renovado. Depois da
avaliacao, basta apagar rasa\\actions\\clinica_sus.db (como promete o termo).

Uso:  python cenarios\\prepara_banco_sus.py
"""
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REAL = RAIZ / "rasa" / "actions" / "clinica.db"
SUS = RAIZ / "rasa" / "actions" / "clinica_sus.db"
DIAS_UTEIS = 10
BLOCOS = [(time(8, 0), time(12, 0)), (time(14, 0), time(18, 0))]
PASSO = timedelta(minutes=30)

if SUS.exists():
    SUS.unlink()
src = sqlite3.connect(f"file:{REAL}?mode=ro", uri=True)
dst = sqlite3.connect(SUS)
src.backup(dst)
src.close()

dst.execute("PRAGMA foreign_keys = ON")
apagados = {}
for tabela in ("lembrete", "consulta", "consentimento_lgpd", "paciente_convenio",
               "log_interacao", "handoff", "paciente"):
    apagados[tabela] = dst.execute(f"DELETE FROM {tabela}").rowcount
liberados = dst.execute("UPDATE horario SET status = 'disponivel' WHERE status = 'reservado'").rowcount
# horarios ja passados nao servem para nada
passados = dst.execute("DELETE FROM horario WHERE data < ?", (date.today().isoformat(),)).rowcount

# horarios livres nos proximos dias uteis
dias, d = [], date.today() + timedelta(days=1)
while len(dias) < DIAS_UTEIS:
    if d.weekday() < 5:
        dias.append(d)
    d += timedelta(days=1)
novos = 0
for (medico_id,) in dst.execute("SELECT id FROM medico").fetchall():
    for dia in dias:
        for ini, fim in BLOCOS:
            t = datetime.combine(dia, ini)
            while t + PASSO <= datetime.combine(dia, fim):
                novos += dst.execute(
                    "INSERT OR IGNORE INTO horario (medico_id, data, hora_inicio, hora_fim, status) "
                    "VALUES (?, ?, ?, ?, 'disponivel')",
                    (medico_id, dia.isoformat(), t.strftime("%H:%M"), (t + PASSO).strftime("%H:%M")),
                ).rowcount
                t += PASSO
dst.commit()

# conferencia
def n(sql):
    return dst.execute(sql).fetchone()[0]

print(f"Banco do SUS: {SUS}")
print("Dados de pacientes apagados:", ", ".join(f"{t}={q}" for t, q in apagados.items()))
print(f"Horarios: {liberados} liberados, {passados} passados removidos, {novos} novos")
print(f"Restam: pacientes={n('SELECT COUNT(*) FROM paciente')}, consultas={n('SELECT COUNT(*) FROM consulta')}, "
      f"logs={n('SELECT COUNT(*) FROM log_interacao')}")
livres = n("SELECT COUNT(*) FROM horario WHERE status = 'disponivel'")
print(f"Horarios livres: {livres} "
      f"(de {dias[0].strftime('%d/%m')} a {dias[-1].strftime('%d/%m/%Y')}); "
      f"medicos={n('SELECT COUNT(*) FROM medico')}, convenios={n('SELECT COUNT(*) FROM convenio')}")
print(f"Integridade: {n('PRAGMA integrity_check')}; chaves estrangeiras quebradas: "
      f"{len(dst.execute('PRAGMA foreign_key_check').fetchall())}")
dst.close()
