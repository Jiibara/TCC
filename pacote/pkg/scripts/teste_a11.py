import sqlite3, re
base = open('ddl_corrigido.sql').read()

def run(ddl, titulo):
    con = sqlite3.connect(':memory:'); con.executescript(ddl)
    cur = con.cursor()
    cur.execute("INSERT INTO medico(nome) VALUES ('Dr. A')")
    cur.execute("INSERT INTO paciente(nome,telefone,data_nascimento) VALUES ('P1','45999990001','1990-01-01'),('P2','45999990002','1991-02-02')")
    cur.execute("INSERT INTO horario(medico_id,data,hora_inicio,hora_fim,status) VALUES (1,'2026-12-01','09:00','09:30','disponivel')")
    out = [titulo]
    def tenta(desc, sql, args=()):
        try: cur.execute(sql, args); out.append(f"  OK    {desc}")
        except sqlite3.IntegrityError as e: out.append(f"  ERRO  {desc} -> {e}")
    tenta("P1 agenda o horario 1", "INSERT INTO consulta(paciente_id,horario_id,status) VALUES (1,1,'agendada')")
    tenta("P2 tenta o MESMO horario (deve falhar: duplo agendamento)", "INSERT INTO consulta(paciente_id,horario_id,status) VALUES (2,1,'agendada')")
    tenta("P1 cancela (status cancelada)", "UPDATE consulta SET status='cancelada' WHERE id=1")
    tenta("horario volta a 'disponivel'", "UPDATE horario SET status='disponivel' WHERE id=1")
    tenta("P2 agenda o horario liberado (deve funcionar)", "INSERT INTO consulta(paciente_id,horario_id,status) VALUES (2,1,'agendada')")
    tenta("P1 tenta agendar de novo o mesmo horario (deve falhar)", "INSERT INTO consulta(paciente_id,horario_id,status) VALUES (1,1,'agendada')")
    return "\n".join(out)

# Versão ORIGINAL (como no dicionário de dados): horario_id NOT NULL UNIQUE
original = base.replace("horario_id     INTEGER NOT NULL REFERENCES horario(id),   -- sem UNIQUE simples",
                        "horario_id     INTEGER NOT NULL UNIQUE REFERENCES horario(id),")
original = re.sub(r"CREATE UNIQUE INDEX ux_consulta_horario_ativo.*?;\n", "", original, flags=re.S)
print(run(original, "[1] ESQUEMA ORIGINAL (UNIQUE simples em consulta.horario_id)"))
print()
print(run(base, "[2] ESQUEMA CORRIGIDO (indice unico parcial)"))
