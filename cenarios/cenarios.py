#!/usr/bin/env python3
"""Testes funcionais (Cap. 5): executa os cenarios da Tabela de testes
funcionais pelo canal REST do Rasa e confere, a cada passo, a resposta do
bot E o banco de dados (somente leitura).

Pre-requisitos:
  - banco de TESTE (nunca o real): rasa\\actions\\clinica_cenarios.db
  - rasa run actions  com  set CLINICA_DB=<caminho do banco de teste>
  - rasa run (com `rest:` no credentials.yml)

Uso (venv ativo):
  python cenarios\\cenarios.py                 # todos os cenarios, 3 execucoes cada
  python cenarios\\cenarios.py --so 3 4        # so os cenarios 3 e 4
  python cenarios\\cenarios.py --execucoes 1   # uma execucao (teste rapido)

Grava cada execucao em cenarios\\resultados.csv e imprime o resumo.
Cada execucao usa um remetente novo (um "paciente" novo), para nao depender
do estado das outras.
"""
import argparse
import csv
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.stdout.reconfigure(line_buffering=True)

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
DB = RAIZ / "rasa" / "actions" / "clinica_cenarios.db"
URL = "http://127.0.0.1:5005/webhooks/rest/webhook"
CSV = AQUI / "resultados.csv"
FIN_AGEND = "agendamento de consultas e atendimento administrativo"
FIN_LEMB = "envio de lembretes e confirmação de presença"


class Falha(Exception):
    pass


# ------------------------------------------------------------------ apoio
def bot(sender, msg, log):
    r = requests.post(URL, json={"sender": sender, "message": msg}, timeout=60)
    if r.status_code != 200:
        raise Falha(f"HTTP {r.status_code} para {msg!r}")
    resp = " | ".join(m.get("text", "") for m in r.json() if isinstance(m, dict))
    log.append(f"> {msg}\n< {resp}")
    return resp


def espera(resp, *trechos):
    """Exige que a resposta contenha pelo menos um dos trechos (sem diferenciar maiusculas)."""
    if not any(t.lower() in resp.lower() for t in trechos):
        raise Falha(f"esperava {trechos!r}, veio {resp[:160]!r}")


def q(sql, args=()):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def checa(cond, msg):
    if not cond:
        raise Falha("banco: " + msg)


def paciente(sender):
    r = q("SELECT * FROM paciente WHERE telegram_chat_id = ?", (sender,))
    return r[0] if r else None


def consents_ativos(pid):
    return {r["finalidade"] for r in q(
        "SELECT finalidade FROM consentimento_lgpd WHERE paciente_id = ? AND revogado_em IS NULL", (pid,))}


def consultas(pid):
    return q("""SELECT c.id, c.status, c.horario_id, h.status AS h_status
                FROM consulta c JOIN horario h ON h.id = c.horario_id
                WHERE c.paciente_id = ? ORDER BY c.id""", (pid,))


def lembretes(consulta_id):
    return q("SELECT * FROM lembrete WHERE consulta_id = ?", (consulta_id,))


def cadastrar(s, log, lembretes_ok=True, pedido="quero marcar uma consulta com cardiologista"):
    """Cadastro completo a partir de um pedido de agendamento; termina na lista de horarios."""
    espera(bot(s, pedido, log), "consentimento", "autoriza")
    espera(bot(s, "sim", log), "lembrete")
    bot(s, "sim" if lembretes_ok else "não", log)
    bot(s, "Paciente Cenario Teste", log)
    bot(s, "10/05/1990", log)
    bot(s, "(45) 99999-1234", log)
    resp = bot(s, "particular", log)
    espera(resp, "Cadastro e consentimento registrados")
    espera(resp, "Encontrei estas opções")
    return resp


def escolher_e_confirmar(s, log, opcao="1"):
    espera(bot(s, opcao, log), "Confirma")
    resp = bot(s, "sim", log)
    return resp


def agendar_cadastrado(s, log):
    """Paciente ja cadastrado: agenda o 1o horario e devolve a consulta."""
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(escolher_e_confirmar(s, log), "agendada com sucesso")
    pac = paciente(s)
    return consultas(pac["id"])[-1]


def preparar(s, log, lembretes_ok=True):
    """Cadastra sem agendar (zera a conversa depois)."""
    cadastrar(s, log, lembretes_ok)
    bot(s, "/restart", log)


# ------------------------------------------------------------------ cenarios
def c01_agendamento(s, log):
    preparar(s, log)
    c = agendar_cadastrado(s, log)
    checa(c["status"] == "agendada", f"consulta {c['status']}")
    checa(c["h_status"] == "reservado", f"horario {c['h_status']}")
    checa(any(l["status_confirmacao"] == "pendente" for l in lembretes(c["id"])), "lembrete nao criado")


def c02_sem_especialidade(s, log):
    preparar(s, log)
    espera(bot(s, "quero agendar uma consulta", log), "especialidade")
    espera(bot(s, "cardiologia", log), "Encontrei estas opções")
    espera(escolher_e_confirmar(s, log), "agendada com sucesso")
    c = consultas(paciente(s)["id"])[-1]
    checa(c["status"] == "agendada" and c["h_status"] == "reservado", "consulta/horario")


def c03_cancelamento(s, log):
    preparar(s, log)
    c = agendar_cadastrado(s, log)
    espera(bot(s, "quero cancelar minha consulta", log), "Você quer cancelar")
    espera(bot(s, "sim", log), "cancelada")
    c2 = [x for x in consultas(paciente(s)["id"]) if x["id"] == c["id"]][0]
    checa(c2["status"] == "cancelada", f"consulta {c2['status']} (deveria ser mantida como cancelada)")
    checa(c2["h_status"] == "disponivel", f"horario {c2['h_status']}")
    checa(all(l["status_confirmacao"] != "pendente" for l in lembretes(c["id"])), "lembrete continua pendente")


def c04_reagendamento(s, log):
    preparar(s, log)
    c = agendar_cadastrado(s, log)
    espera(bot(s, "quero remarcar minha consulta", log), "Encontrei estas opções")
    espera(escolher_e_confirmar(s, log, "2"), "reagendada com sucesso")
    c2 = [x for x in consultas(paciente(s)["id"]) if x["id"] == c["id"]][0]
    checa(c2["status"] == "reagendada", f"consulta {c2['status']}")
    checa(c2["horario_id"] != c["horario_id"], "horario nao mudou")
    checa(c2["h_status"] == "reservado", "horario novo nao reservado")
    antigo = q("SELECT status FROM horario WHERE id = ?", (c["horario_id"],))[0]["status"]
    checa(antigo == "disponivel", f"horario antigo {antigo}")


def c05_convenio(s, log):
    espera(bot(s, "vocês aceitam Unimed?", log), "Unimed")


def c08_cadastro_integral(s, log):
    cadastrar(s, log, True)
    pac = paciente(s)
    checa(pac is not None, "paciente nao gravado")
    checa(consents_ativos(pac["id"]) == {FIN_AGEND, FIN_LEMB}, f"consentimentos {consents_ativos(pac['id'])}")


def c09_consentimento_parcial(s, log):
    cadastrar(s, log, False)
    pac = paciente(s)
    checa(consents_ativos(pac["id"]) == {FIN_AGEND}, f"consentimentos {consents_ativos(pac['id'])}")
    espera(escolher_e_confirmar(s, log), "agendada com sucesso")
    c = consultas(pac["id"])[-1]
    checa(not lembretes(c["id"]), "lembrete criado sem consentimento")


def c10_recusa(s, log):
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "consentimento", "autoriza")
    espera(bot(s, "não", log), "Nenhuma informação foi guardada")
    checa(paciente(s) is None, "paciente gravado apesar da recusa")


def c11_respostas_invalidas(s, log):
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "consentimento", "autoriza")
    bot(s, "sim", log)
    bot(s, "sim", log)
    espera(bot(s, "Jo", log), "nome completo")
    bot(s, "Joana Cenario Teste", log)
    espera(bot(s, "31/02/1990", log), "Não consegui entender a data")
    bot(s, "10/05/1990", log)
    espera(bot(s, "123", log), "Telefone inválido")
    bot(s, "(45) 99999-1234", log)
    espera(bot(s, "Plano Inexistente XYZ", log), "Não reconheci esse convênio")
    espera(bot(s, "particular", log), "Cadastro e consentimento registrados")
    pac = paciente(s)
    checa(pac is not None and pac["data_nascimento"] == "1990-05-10" and pac["telefone"] == "45999991234",
          "dados gravados incorretos")


def c12_interrupcao(s, log):
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "consentimento", "autoriza")
    bot(s, "sim", log)
    bot(s, "sim", log)
    bot(s, "Joana Cenario Teste", log)
    espera(bot(s, "cancelar", log), "interrompi")
    checa(paciente(s) is None, "paciente gravado apesar da interrupcao")


def c13_atualizacao(s, log):
    preparar(s, log)
    espera(bot(s, "quero atualizar meu cadastro", log), "Seus dados atuais")
    bot(s, "manter", log)                 # nome
    bot(s, "manter", log)                 # nascimento
    bot(s, "(45) 98888-7777", log)        # telefone novo
    espera(bot(s, "manter", log), "dados foram atualizados")  # convenio
    pac = paciente(s)
    checa(pac["telefone"] == "45988887777", f"telefone {pac['telefone']}")
    checa(pac["nome"] == "Paciente Cenario Teste" and pac["data_nascimento"] == "1990-05-10", "dados mantidos alterados")


def c14_revogacao(s, log):
    preparar(s, log)
    pid = paciente(s)["id"]
    espera(bot(s, "quero revogar meu consentimento", log), "Consentimento revogado")
    checa(not consents_ativos(pid), "consentimento continua ativo")
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "autorize novamente")
    espera(bot(s, "sim", log), "lembrete")
    espera(bot(s, "sim", log), "Cadastro e consentimento registrados")
    checa(consents_ativos(pid) == {FIN_AGEND, FIN_LEMB}, "novo consentimento nao registrado")


def c15_politica(s, log):
    espera(bot(s, "política de privacidade", log), "Política de privacidade")


def c16_fora_escopo(s, log):
    espera(bot(s, "qual o resultado do jogo de ontem?", log), "só consigo ajudar")


def c17_emergencia(s, log):
    espera(bot(s, "é urgente, preciso de ajuda agora", log), "SAMU")
    checa(paciente(s) is None, "cadastro iniciado na emergencia")


def c18_horario_liberado(s, log):
    a, b, c, d = (s + x for x in ("A", "B", "C", "D"))
    preparar(a, log)
    ca = agendar_cadastrado(a, log)
    h = ca["horario_id"]
    espera(bot(a, "quero cancelar minha consulta", log), "Você quer cancelar")
    espera(bot(a, "sim", log), "cancelada")
    # outro paciente pega o mesmo horario liberado (o primeiro da lista)
    preparar(b, log)
    cb = agendar_cadastrado(b, log)
    checa(cb["horario_id"] == h, f"B pegou horario {cb['horario_id']}, esperado {h}")
    checa([x for x in consultas(paciente(a)["id"]) if x["id"] == ca["id"]][0]["status"] == "cancelada",
          "consulta cancelada de A nao foi mantida")
    # disputa: C e D escolhem o mesmo horario; so o primeiro a confirmar consegue
    preparar(c, log)
    preparar(d, log)
    espera(bot(c, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(d, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(c, "1", log), "Confirma")
    espera(bot(d, "1", log), "Confirma")
    espera(bot(c, "sim", log), "agendada com sucesso")
    espera(bot(d, "sim", log), "reservado por outra pessoa")
    checa(not consultas(paciente(d)["id"]), "D ficou com consulta")
    ativos = q("""SELECT horario_id, COUNT(*) n FROM consulta WHERE status IN ('agendada','reagendada')
                  GROUP BY horario_id HAVING n > 1""")
    checa(not ativos, "horario com mais de uma consulta ativa")


def c19_confirmo(s, log):
    """'confirmo' (em vez de 'sim') respondendo a pedidos de confirmacao.
    Falha encontrada no teste manual: era lido como confirmacao de lembrete."""
    preparar(s, log)
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(s, "1", log), "Confirma")
    espera(bot(s, "confirmo", log), "agendada com sucesso")
    c = consultas(paciente(s)["id"])[-1]
    checa(c["status"] == "agendada" and c["h_status"] == "reservado", "consulta/horario")
    checa(any(l["status_confirmacao"] == "pendente" for l in lembretes(c["id"])), "lembrete nao criado")
    espera(bot(s, "quero cancelar minha consulta", log), "Você quer cancelar")
    espera(bot(s, "confirmo", log), "cancelada")
    c2 = [x for x in consultas(paciente(s)["id"]) if x["id"] == c["id"]][0]
    checa(c2["status"] == "cancelada" and c2["h_status"] == "disponivel", "cancelamento")


def ativas(pid):
    return [c for c in consultas(pid) if c["status"] in ("agendada", "reagendada")]


def c20_troca_confirmada(s, log):
    """Pedido de troca de horario sem a palavra 'remarcar' (frase elaborada pelo pesquisador)."""
    preparar(s, log)
    c = agendar_cadastrado(s, log)
    r = bot(s, "Na verdade não... dá pra marcar às 15:30?", log)
    if "especialidade" in r.lower():  # a frase nao diz a especialidade: o bot pergunta
        r = bot(s, "cardiologia", log)
    espera(r, "Encontrei estas opções")
    espera(bot(s, "1", log), "trocar")
    espera(bot(s, "sim", log), "reagendada com sucesso")
    at = ativas(paciente(s)["id"])
    checa(len(at) == 1, f"{len(at)} consultas ativas (esperado 1)")
    checa(at[0]["id"] == c["id"] and at[0]["horario_id"] != c["horario_id"], "consulta nao foi remarcada")


def c21_troca_recusada_nova(s, log):
    preparar(s, log)
    agendar_cadastrado(s, log)
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(s, "1", log), "trocar")
    espera(bot(s, "não", log), "nova consulta")
    espera(bot(s, "sim", log), "agendada com sucesso")
    checa(len(ativas(paciente(s)["id"])) == 2, "deveria haver 2 consultas ativas")


def c22_outro_horario(s, log):
    preparar(s, log)
    r1 = bot(s, "quero marcar uma consulta com cardiologista", log)
    espera(r1, "Encontrei estas opções")
    r2 = bot(s, "outro horário", log)
    espera(r2, "Encontrei estas opções")
    op1 = [l for l in r1.split("\n") if l[:2] in ("1.", "2.", "3.")]
    op2 = [l for l in r2.split("\n") if l[:2] in ("1.", "2.", "3.")]
    checa(op1 and op2 and not set(x[3:] for x in op1) & set(x[3:] for x in op2), "repetiu opcoes ja oferecidas")
    espera(escolher_e_confirmar(s, log), "agendada com sucesso")


def c23_cancelar_lista_aberta(s, log):
    preparar(s, log)
    c = agendar_cadastrado(s, log)
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(s, "cancelar", log), "Você quer cancelar a consulta")
    espera(bot(s, "sim", log), "cancelada")
    c2 = [x for x in consultas(paciente(s)["id"]) if x["id"] == c["id"]][0]
    checa(c2["status"] == "cancelada", f"consulta {c2['status']}")


URL_TELEGRAM = "http://127.0.0.1:5005/webhooks/telegram_custom/webhook"


def c24_mensagem_repetida(s, log):
    """O Telegram reenvia a mesma atualizacao (mesmo update_id) quando a resposta demora:
    ela deve ser processada uma unica vez."""
    preparar(s, log)
    pid = paciente(s)["id"]
    antes = len(q("SELECT id FROM log_interacao WHERE paciente_id = ? AND intent_detectado = 'consultarAgendamento'", (pid,)))
    upd = {"update_id": int(time.time() * 1000) % 2_000_000_000,
           "message": {"message_id": 1, "chat": {"id": s}, "text": "qual data eu agendei?"}}
    for _ in range(2):
        r = requests.post(URL_TELEGRAM, json=upd, timeout=60)
        log.append(f"> [telegram update_id={upd['update_id']}] qual data eu agendei?\n< HTTP {r.status_code}")
        checa(r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(4)  # o processamento pode seguir em segundo plano
    depois = len(q("SELECT id FROM log_interacao WHERE paciente_id = ? AND intent_detectado = 'consultarAgendamento'", (pid,)))
    checa(depois - antes == 1, f"mensagem processada {depois - antes} vezes (esperado 1)")


def ativas_por_data(pid):
    """Consultas ativas do paciente na ordem em que o bot as lista (data e hora)."""
    return q("""SELECT c.id, c.status, c.horario_id FROM consulta c JOIN horario h ON h.id = c.horario_id
                WHERE c.paciente_id = ? AND c.status IN ('agendada','reagendada')
                ORDER BY h.data, h.hora_inicio""", (pid,))


def duas_consultas(s, log):
    """Paciente com duas consultas futuras: agenda uma e, na pergunta de troca, mantém as duas."""
    preparar(s, log)
    agendar_cadastrado(s, log)
    espera(bot(s, "quero marcar uma consulta com cardiologista", log), "Encontrei estas opções")
    espera(bot(s, "1", log), "trocar")
    espera(bot(s, "não", log), "nova consulta")
    espera(bot(s, "sim", log), "agendada com sucesso")
    at = ativas_por_data(paciente(s)["id"])
    checa(len(at) == 2, f"{len(at)} consultas ativas na preparacao (esperado 2)")
    return at


def c25_cancelar_com_duas(s, log):
    """Achado do parecer (A2): com duas consultas, o paciente escolhe qual cancelar."""
    c1, c2 = duas_consultas(s, log)
    espera(bot(s, "quero cancelar minha consulta", log), "Qual você quer cancelar")
    espera(bot(s, "2", log), "Você quer cancelar a consulta")
    espera(bot(s, "sim", log), "cancelada")
    st = {c["id"]: c["status"] for c in consultas(paciente(s)["id"])}
    checa(st[c2["id"]] == "cancelada", f"consulta escolhida ficou {st[c2['id']]}")
    checa(st[c1["id"]] in ("agendada", "reagendada"), f"a outra consulta ficou {st[c1['id']]}")


def c26_remarcar_com_duas(s, log):
    """Achado do parecer (A2): com duas consultas, o paciente escolhe qual remarcar."""
    c1, c2 = duas_consultas(s, log)
    espera(bot(s, "quero remarcar minha consulta", log), "Qual você quer remarcar")
    espera(bot(s, "1", log), "Encontrei estas opções")
    espera(escolher_e_confirmar(s, log), "reagendada com sucesso")
    depois = {c["id"]: c for c in consultas(paciente(s)["id"])}
    checa(depois[c1["id"]]["status"] == "reagendada" and depois[c1["id"]]["horario_id"] != c1["horario_id"],
          "a consulta escolhida nao foi remarcada")
    checa(depois[c2["id"]]["horario_id"] == c2["horario_id"] and depois[c2["id"]]["status"] == "agendada",
          "a outra consulta foi alterada")


CENARIOS = [
    (1, "Agendamento de nova consulta", c01_agendamento),
    (2, "Agendamento sem especialidade informada", c02_sem_especialidade),
    (3, "Cancelamento de consulta", c03_cancelamento),
    (4, "Reagendamento de consulta", c04_reagendamento),
    (5, "Consulta de convênios aceitos", c05_convenio),
    (8, "Cadastro com consentimento integral", c08_cadastro_integral),
    (9, "Cadastro com consentimento parcial (sem lembretes)", c09_consentimento_parcial),
    (10, "Recusa do consentimento (nenhum dado gravado)", c10_recusa),
    (11, "Respostas inválidas no cadastro", c11_respostas_invalidas),
    (12, "Interrupção do cadastro (cancelar)", c12_interrupcao),
    (13, "Atualização cadastral", c13_atualizacao),
    (14, "Revogação do consentimento e novo aceite", c14_revogacao),
    (15, "Política de privacidade", c15_politica),
    (16, "Mensagens fora do escopo (fallback)", c16_fora_escopo),
    (17, "Emergência (orientação ao SAMU)", c17_emergencia),
    (18, "Novo agendamento de horário liberado por cancelamento", c18_horario_liberado),
    (19, "Confirmação com \"confirmo\" (agendamento e cancelamento)", c19_confirmo),
    (20, "Pedido de troca sem \"remarcar\", com troca confirmada", c20_troca_confirmada),
    (21, "Pedido de troca recusado e nova consulta mantida", c21_troca_recusada_nova),
    (22, "\"Outro horário\" com a lista aberta", c22_outro_horario),
    (23, "\"Cancelar\" com a lista aberta", c23_cancelar_lista_aberta),
    (24, "Mensagem repetida pelo Telegram (mesmo update_id)", c24_mensagem_repetida),
    (25, "Cancelamento com duas consultas ativas (escolha)", c25_cancelar_com_duas),
    (26, "Reagendamento com duas consultas ativas (escolha)", c26_remarcar_com_duas),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execucoes", type=int, default=3)
    ap.add_argument("--so", type=int, nargs="*", help="numeros dos cenarios")
    ap.add_argument("--csv", help="arquivo de resultados (padrao: cenarios\\resultados.csv)")
    args = ap.parse_args()
    global CSV
    if args.csv:
        CSV = Path(args.csv)
    if not DB.exists():
        sys.exit(f"Banco de teste nao encontrado: {DB}")

    marca = datetime.now().strftime("%m%d%H%M%S")
    novo = not CSV.exists()
    f = open(CSV, "a", encoding="utf-8", newline="")
    w = csv.writer(f)
    if novo:
        w.writerow(["quando", "cenario", "nome", "execucao", "sucesso", "detalhe"])
    resumo = {}
    for num, nome, fn in CENARIOS:
        if args.so and num not in args.so:
            continue
        for e in range(1, args.execucoes + 1):
            s = f"cen{num:02d}e{e}_{marca}"
            log = []
            try:
                fn(s, log)
                ok, det = True, ""
            except Falha as ex:
                ok, det = False, str(ex)
            except Exception as ex:  # erro inesperado (rede, etc.)
                ok, det = False, f"{ex.__class__.__name__}: {ex}"
            w.writerow([datetime.now().isoformat(timespec="seconds"), num, nome, e, int(ok), det])
            f.flush()
            resumo.setdefault((num, nome), []).append(ok)
            print(f"[{num:02d}] {nome} - execução {e}: {'OK' if ok else 'FALHA: ' + det}")
            if not ok:
                print("     conversa:\n       " + "\n       ".join(log[-6:]).replace("\n", "\n       "))
    f.close()

    print("\n=== RESUMO ===")
    tot_e = tot_s = 0
    for (num, nome), oks in resumo.items():
        tot_e += len(oks)
        tot_s += sum(oks)
        print(f"{num:02d} {nome:<55} {sum(oks)}/{len(oks)}")
    if tot_e:
        print(f"TOTAL: {tot_s}/{tot_e} = {100 * tot_s / tot_e:.1f}%")


if __name__ == "__main__":
    main()
