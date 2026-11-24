"""
Envio automático de lembretes (RF08) pelo Telegram.

Processo separado do Rasa: a cada minuto olha a tabela `lembrete` e envia
os que já venceram. A confirmação de presença (RF09) volta pelo chat normal
(intent confirmarLembrete -> action_confirmar_presenca).

    set TELEGRAM_BOT_TOKEN=123456:ABC...        (Windows, cmd)
    python enviar_lembretes.py                  (fica rodando)
    python enviar_lembretes.py --uma-vez        (processa uma vez e sai; bom para testar)

Fica na pasta `rasa` (NÃO dentro de `rasa/actions`) e importa constantes de actions/actions.py.
O token vem de variável de ambiente para não ficar escrito no código.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import requests

import actions.actions as bot  # garante finalidade e formatação iguais às do bot
from actions.actions import FINALIDADE_LEMBRETES, STATUS_ATIVOS, consentimento_ativo, formatar_data_hora

# Banco: variável CLINICA_DB, senão rasa/actions/clinica.db (onde o bot o mantém).
_AQUI = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CLINICA_DB") or os.path.join(_AQUI, "actions", "clinica.db")
bot.DB_PATH = DB_PATH  # a checagem de consentimento do bot usa o MESMO banco deste script

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
INTERVALO_S = 60
REENVIO_HORAS_ANTES = 3      # 2º lembrete se ainda não confirmou
REENVIO_MIN_APOS_ENVIO_H = 2  # e só se o 1º foi enviado há pelo menos isso

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lembretes")

_ATIVOS = ",".join(f"'{s}'" for s in STATUS_ATIVOS)


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


def enviar_telegram(chat_id: str, texto: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("Telegram recusou o envio (HTTP %s)", r.status_code)
        return r.status_code == 200
    except requests.RequestException as exc:
        # não loga a exceção inteira: a mensagem dela contém a URL com o token
        log.warning("Falha de rede ao enviar (%s)", type(exc).__name__)
        return False


def processar() -> None:
    agora = datetime.now()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # 1) envia os lembretes vencidos
        devidos = conn.execute(
            f"""
            SELECT l.id AS lid, c.id AS cid, c.status AS cstatus, p.id AS pid, p.nome,
                   p.telegram_chat_id AS chat, h.data, h.hora_inicio, m.nome AS medico
            FROM lembrete l
            JOIN consulta c ON c.id = l.consulta_id
            JOIN paciente p ON p.id = c.paciente_id
            JOIN horario h ON h.id = c.horario_id
            JOIN medico m ON m.id = h.medico_id
            WHERE l.status_confirmacao = 'pendente' AND l.enviado_em IS NULL AND l.data_envio <= ?
            """,
            (_iso(agora),),
        ).fetchall()

        for r in devidos:
            quando = datetime.fromisoformat(f"{r['data']} {r['hora_inicio']}")
            if r["cstatus"] not in STATUS_ATIVOS or quando <= agora:
                motivo = "consulta inativa ou já passou"
            elif not r["chat"]:
                motivo = "paciente sem chat do Telegram"
            elif not consentimento_ativo(r["pid"], FINALIDADE_LEMBRETES):
                motivo = "sem consentimento ativo para lembretes"
            else:
                motivo = None

            if motivo:
                conn.execute("UPDATE lembrete SET status_confirmacao = 'ignorado' WHERE id = ?", (r["lid"],))
                conn.commit()
                log.info("Lembrete %s ignorado: %s", r["lid"], motivo)
                continue

            primeiro_nome = (r["nome"] or "").split(" ")[0]
            texto = (
                f"Olá, {primeiro_nome}! Lembrete da sua consulta com {r['medico']}: "
                f"{formatar_data_hora(r['data'], r['hora_inicio'])}.\n"
                "Para confirmar sua presença, responda CONFIRMO. "
                "Se não puder comparecer, responda CANCELAR."
            )
            if enviar_telegram(r["chat"], texto):
                conn.execute("UPDATE lembrete SET enviado_em = ? WHERE id = ?", (_iso(datetime.now()), r["lid"]))
                conn.commit()
                log.info("Lembrete %s enviado", r["lid"])
            # se falhar, continua pendente e é tentado de novo no próximo ciclo

        # 2) segundo lembrete (reenvio) se ainda não confirmou e a consulta está perto
        candidatas = conn.execute(
            f"""
            SELECT c.id AS cid, h.data, h.hora_inicio,
                   (SELECT MAX(l.enviado_em) FROM lembrete l WHERE l.consulta_id = c.id) AS ultimo_envio
            FROM consulta c
            JOIN horario h ON h.id = c.horario_id
            WHERE c.status IN ({_ATIVOS})
              AND (SELECT COUNT(*) FROM lembrete l WHERE l.consulta_id = c.id) = 1
              AND EXISTS (SELECT 1 FROM lembrete l WHERE l.consulta_id = c.id
                          AND l.status_confirmacao = 'pendente' AND l.enviado_em IS NOT NULL)
            """
        ).fetchall()
        for c in candidatas:
            quando = datetime.fromisoformat(f"{c['data']} {c['hora_inicio']}")
            enviado = datetime.fromisoformat(c["ultimo_envio"])
            if (timedelta(0) < quando - agora <= timedelta(hours=REENVIO_HORAS_ANTES)
                    and agora - enviado >= timedelta(hours=REENVIO_MIN_APOS_ENVIO_H)):
                conn.execute(
                    "INSERT INTO lembrete (consulta_id, data_envio, status_confirmacao) VALUES (?, ?, 'pendente')",
                    (c["cid"], _iso(agora)),
                )
                conn.commit()
                log.info("Reenvio programado para a consulta %s", c["cid"])

        # 3) lembretes enviados e nunca confirmados, cuja consulta já passou -> 'ignorado'
        conn.execute(
            """
            UPDATE lembrete SET status_confirmacao = 'ignorado'
            WHERE status_confirmacao = 'pendente' AND enviado_em IS NOT NULL
              AND consulta_id IN (SELECT c.id FROM consulta c JOIN horario h ON h.id = c.horario_id
                                  WHERE (h.data || ' ' || h.hora_inicio) < ?)
            """,
            (agora.strftime("%Y-%m-%d %H:%M"),),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    if not TOKEN:
        raise SystemExit("Defina a variável de ambiente TELEGRAM_BOT_TOKEN antes de rodar.")
    if not os.path.exists(DB_PATH):  # evita o sqlite criar um banco vazio sem avisar
        raise SystemExit(f"Banco não encontrado: {DB_PATH}\nDefina CLINICA_DB com o caminho do clinica.db.")
    log.info("Banco em uso: %s", DB_PATH)
    if "--uma-vez" in sys.argv:
        processar()
        return
    log.info("Serviço de lembretes ativo (ciclo de %ss). Ctrl+C para parar.", INTERVALO_S)
    while True:
        try:
            processar()
        except sqlite3.Error as exc:
            log.error("Erro de banco: %s", exc)
        time.sleep(INTERVALO_S)


if __name__ == "__main__":
    main()