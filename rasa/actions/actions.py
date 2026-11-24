"""
Custom Actions do chatbot de agendamento clínico.

Cada ação aqui corresponde diretamente a um Requisito Funcional (RF)
definido na seção 4.2 do TCC. A persistência usa SQLite (arquivo
`clinica.db`, gerado a partir de schema.sql) para manter o requisito
de execução 100% local / on-premise (RNF01).

Fluxo de agendamento/reagendamento agora tem confirmação explícita:
1. action_consultar_disponibilidade busca horários e PERGUNTA se
   confirma, guardando o horário candidato em `horario_id_disponivel`
   e o que estava sendo feito (agendar/reagendar) em `acao_pendente`.
2. Só quando o usuário responde afirmativamente (intent `afirmar`),
   action_confirmar_agendamento de fato grava no banco.
3. Se o usuário responder `negar`, a sugestão é descartada.

Mudanças desta revisão: consulta ao próprio agendamento, handoff com
estado persistido (tabela `handoff`), cancelar/reagendar também sobre
consultas 'reagendada', checagem de disponibilidade ao reagendar,
rollback em erros de banco e datas formatadas em pt-BR.
"""

import sqlite3
import os
import re
import unicodedata
import difflib
from datetime import datetime, timedelta, date
from typing import Any, Text, Dict, List, Optional

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, ActiveLoop, FollowupAction
from rasa_sdk.forms import FormValidationAction

# Caminho absoluto, sempre relativo a este arquivo — assim funciona
# não importa de qual pasta o comando `rasa run actions` for executado.
# A variável CLINICA_DB permite apontar outro banco (ex.: uma cópia
# para medições), como em enviar_lembretes.py e migrar_banco.py.
DB_PATH = os.environ.get("CLINICA_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "clinica.db"
)

DIAS_SEMANA = {
    "segunda": 0, "segunda-feira": 0, "seg": 0,
    "terca": 1, "terca-feira": 1, "ter": 1,
    "quarta": 2, "quarta-feira": 2, "qua": 2,
    "quinta": 3, "quinta-feira": 3, "qui": 3,
    "sexta": 4, "sexta-feira": 4, "sex": 4,
    "sabado": 5, "sab": 5,
    "domingo": 6, "dom": 6,
}


def _remover_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_data_relativa(texto: Optional[str], hoje: Optional[date] = None) -> Optional[date]:
    """Converte expressões comuns em português (relativas ou
    explícitas) para uma data concreta. Retorna None se não
    reconhecer nada — nesse caso, quem chamar deve simplesmente não
    filtrar por data (comportamento atual, sem quebrar nada)."""
    if not texto:
        return None

    hoje = hoje or date.today()
    t = _remover_acentos(texto.strip().lower())

    # Data explícita dd/mm ou dd/mm/yyyy
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = int(m.group(3)) if m.group(3) else hoje.year
        if ano < 100:
            ano += 2000
        try:
            candidata = date(ano, mes, dia)
            if not m.group(3) and candidata < hoje:
                candidata = date(ano + 1, mes, dia)
            return candidata
        except ValueError:
            pass

    # "dia 01", "dia 5" — assume o mês atual, ou o próximo se já passou
    m_dia = re.search(r"\bdia\s+(\d{1,2})\b", t)
    if m_dia:
        dia = int(m_dia.group(1))
        try:
            candidata = hoje.replace(day=dia)
            if candidata < hoje:
                mes, ano = (hoje.month % 12) + 1, hoje.year + (1 if hoje.month == 12 else 0)
                candidata = date(ano, mes, dia)
            return candidata
        except ValueError:
            pass

    if "depois de amanha" in t:
        return hoje + timedelta(days=2)
    if "amanha" in t:
        return hoje + timedelta(days=1)
    if "hoje" in t:
        return hoje
    if "semana que vem" in t or "proxima semana" in t:
        return hoje + timedelta(days=7)

    for nome, weekday_alvo in DIAS_SEMANA.items():
        if nome in t:
            dias_ate = (weekday_alvo - hoje.weekday()) % 7
            dias_ate = dias_ate or 7  # se for hoje, considera a próxima ocorrência
            return hoje + timedelta(days=dias_ate)

    return None


MARCADORES_CANCELAR_ESCOLHA = [
    "nenhuma", "nenhum", "nenhuma dessas", "nenhuma delas",
]
# Com a lista aberta, pedir mais opções mostra as próximas (sem repetir).
MARCADORES_MAIS_OPCOES = [
    "outro dia", "outra opcao", "outras opcoes", "outro horario", "outros horarios",
    "mais horarios", "mais opcoes", "outra data", "outras datas",
]
# Com a lista aberta, "cancelar" pode ser o pedido de cancelar a consulta
# que o paciente já tem (ActionSelecionarHorario decide).
MARCADORES_CANCELAR_CONSULTA = ["cancelar", "cancela", "desmarcar", "desmarca"]

ORDINAIS = {
    "primeira": 0, "primeiro": 0, "1a": 0, "1o": 0,
    "segunda": 1, "segundo": 1, "2a": 1, "2o": 1,
    "terceira": 2, "terceiro": 2, "3a": 2, "3o": 2,
    "ultima": -1, "ultimo": -1,
}


def casar_selecao_horario(texto: str, candidatos: List[Dict[str, Any]],
                          marcadores: bool = True) -> Optional[Any]:
    """Interpreta a resposta livre do paciente e identifica qual dos
    horários oferecidos ele quer — por número/ordinal, pelo horário
    mencionado (ex.: 08:30) ou pelo nome do médico. Retorna o `id` do
    horário escolhido, 'MAIS' se o paciente pediu outras opções,
    'CANCELAR_CONSULTA' se pediu para cancelar/desmarcar, 'CANCELAR' se
    recusou as opções, ou None se não foi possível identificar.
    Com marcadores=False, só identifica a opção (usado na escolha da consulta)."""
    if not candidatos:
        return None

    t = _remover_acentos(texto.strip().lower())

    if marcadores:
        if any(m in t for m in MARCADORES_MAIS_OPCOES):
            return "MAIS"
        if any(re.search(rf"\b{m}\b", t) for m in MARCADORES_CANCELAR_CONSULTA):
            return "CANCELAR_CONSULTA"
        if t in ("nao", "n", "nao quero") or any(m in t for m in MARCADORES_CANCELAR_ESCOLHA):
            return "CANCELAR"

    # Número simples (1, 2, 3) — checa antes dos ordinais por extenso
    m_num = re.search(r"(?<!\d)([1-3])(?!\d)", t)
    if m_num:
        idx = int(m_num.group(1)) - 1
        if 0 <= idx < len(candidatos):
            return candidatos[idx]["id"]

    for palavra, idx in ORDINAIS.items():
        if re.search(rf"\b{re.escape(palavra)}\b", t):
            try:
                return candidatos[idx]["id"]
            except IndexError:
                pass

    # Horário mencionado: 08:30, 8h30, 8h, às 8, as 9
    hora, minuto = None, None
    m_hora = re.search(r"\b(\d{1,2})[:h](\d{2})?\b", t)
    if m_hora:
        hora = int(m_hora.group(1))
        minuto = int(m_hora.group(2)) if m_hora.group(2) else None
    else:
        m_hora_solta = re.search(r"\bas\s+(\d{1,2})\b", t)
        if m_hora_solta:
            hora = int(m_hora_solta.group(1))

    if hora is not None:
        if minuto is not None:
            alvo = f"{hora:02d}:{minuto:02d}"
            correspondentes = [c for c in candidatos if c["hora_inicio"] == alvo]
            if len(correspondentes) == 1:
                return correspondentes[0]["id"]
        correspondentes_hora = [c for c in candidatos if c["hora_inicio"].startswith(f"{hora:02d}:")]
        if len(correspondentes_hora) == 1:
            return correspondentes_hora[0]["id"]

    # Nome do médico mencionado
    correspondentes_medico = [
        c for c in candidatos
        if _remover_acentos(c["medico"]).lower() in t
        or any(parte in t for parte in _remover_acentos(c["medico"]).lower().split() if len(parte) > 3)
    ]
    if len(correspondentes_medico) == 1:
        return correspondentes_medico[0]["id"]

    return None


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


STATUS_ATIVOS = ("agendada", "reagendada")
_ATIVOS_SQL = ",".join(f"'{x}'" for x in STATUS_ATIVOS)

DIAS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
           "sexta-feira", "sábado", "domingo"]


def formatar_data_hora(data_iso: str, hora: str) -> str:
    """'2026-10-08', '14:30' -> 'quinta-feira, 08/10/2026, às 14:30'."""
    try:
        d = date.fromisoformat(str(data_iso))
    except ValueError:
        return f"{data_iso} às {hora}"
    return f"{DIAS_PT[d.weekday()]}, {d.strftime('%d/%m/%Y')}, às {hora}"


def resolver_paciente_id(tracker: Tracker) -> Optional[int]:
    """Ponto único para descobrir QUEM é o paciente: mapeia o
    `tracker.sender_id` (chat_id do Telegram) para a tabela `paciente`.
    Devolve None se a pessoa ainda não fez o cadastro."""
    pac = buscar_paciente_por_chat(tracker.sender_id)
    return pac["id"] if pac else None


def resolver_consulta_ativa(tracker: Tracker, paciente_id: int) -> Optional[int]:
    """Consulta ativa do paciente. Usa o slot `consulta_id` só se ela
    ainda estiver ativa no banco (o slot pode apontar para uma consulta
    já cancelada); senão cai para a mais recente ativa."""
    consulta_id = tracker.get_slot("consulta_id")
    if consulta_id:
        conn = get_connection()
        row = conn.execute(
            f"SELECT id FROM consulta WHERE id = ? AND status IN ({_ATIVOS_SQL})",
            (consulta_id,),
        ).fetchone()
        conn.close()
        if row:
            return row["id"]
    return find_active_consulta_id(paciente_id)


def buscar_detalhes_consulta(cur: sqlite3.Cursor, consulta_id: int):
    return cur.execute(
        """
        SELECT c.id, h.data, h.hora_inicio, m.nome AS medico,
               (SELECT e.nome FROM medico_especialidade me
                  JOIN especialidade e ON e.id = me.especialidade_id
                 WHERE me.medico_id = m.id
                 ORDER BY me.principal DESC LIMIT 1) AS especialidade
        FROM consulta c
        JOIN horario h ON h.id = c.horario_id
        JOIN medico m ON m.id = h.medico_id
        WHERE c.id = ?
        """,
        (consulta_id,),
    ).fetchone()


def normalizar_especialidade(valor: Optional[str], cur: sqlite3.Cursor) -> Optional[str]:
    """Corrige erros de digitação comparando o que o usuário disse com
    os nomes de especialidade cadastrados (ex.: 'caridiologistas' ->
    'Cardiologia'). Se não achar nada parecido o suficiente, devolve o
    valor original sem alterar (mantém o comportamento de busca por
    substring como fallback)."""
    if not valor:
        return valor
    nomes = [r[0] for r in cur.execute("SELECT nome FROM especialidade").fetchall()]
    candidatos = difflib.get_close_matches(valor, nomes, n=1, cutoff=0.5)
    return candidatos[0] if candidatos else valor


def find_active_consulta_id(paciente_id: int):
    """Localiza a consulta 'agendada'/'reagendada' mais recente do
    paciente, usada como fallback quando o slot consulta_id ainda não
    foi definido na conversa atual."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM consulta
        WHERE paciente_id = ? AND status IN ('agendada', 'reagendada')
        ORDER BY criado_em DESC LIMIT 1
        """,
        (paciente_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None


def consultas_futuras(paciente_id: Optional[int]) -> List[Dict[str, Any]]:
    """Consultas ativas do paciente que ainda não aconteceram, por data e hora.
    Com mais de uma, cancelar e remarcar perguntam qual (parecer, item A2)."""
    if not paciente_id:
        return []
    agora = datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT c.id, h.data, h.hora_inicio, m.nome AS medico,
                   (SELECT e.nome FROM medico_especialidade me
                      JOIN especialidade e ON e.id = me.especialidade_id
                     WHERE me.medico_id = m.id
                     ORDER BY me.principal DESC LIMIT 1) AS especialidade
            FROM consulta c
            JOIN horario h ON h.id = c.horario_id
            JOIN medico m ON m.id = h.medico_id
            WHERE c.paciente_id = ? AND c.status IN ({_ATIVOS_SQL})
              AND (h.data > ? OR (h.data = ? AND h.hora_inicio > ?))
            ORDER BY h.data, h.hora_inicio
            """,
            (paciente_id, agora.date().isoformat(), agora.date().isoformat(), agora.strftime("%H:%M")),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def listar_consultas(cands: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{i+1}. {c['medico']} ({c['especialidade']}) — {formatar_data_hora(c['data'], c['hora_inicio'])}"
        for i, c in enumerate(cands)
    )


def pedir_escolha_consulta(futuras: List[Dict[str, Any]], acao: str) -> List[Dict[Text, Any]]:
    """Abre o formulário em que o paciente escolhe qual consulta cancelar/remarcar."""
    return [
        SlotSet("consultas_candidatas", futuras),
        SlotSet("acao_consulta", acao),
        SlotSet("consulta_escolhida", None),
        SlotSet("tentativas_escolha", 0.0),
        SlotSet("horario_id_disponivel", None),
        SlotSet("troca_consulta_id", None),
        FollowupAction("escolha_consulta_form"),
    ]


def consulta_ativa_futura(paciente_id: Optional[int]) -> Optional[int]:
    """Próxima consulta ativa do paciente que ainda não aconteceu. Usada
    para perguntar se um novo pedido é, na verdade, uma troca de horário
    (consultas passadas continuam com status 'agendada' no banco)."""
    if not paciente_id:
        return None
    agora = datetime.now()
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT c.id FROM consulta c JOIN horario h ON h.id = c.horario_id
            WHERE c.paciente_id = ? AND c.status IN ({_ATIVOS_SQL})
              AND (h.data > ? OR (h.data = ? AND h.hora_inicio > ?))
            ORDER BY h.data, h.hora_inicio LIMIT 1
            """,
            (paciente_id, agora.date().isoformat(), agora.date().isoformat(), agora.strftime("%H:%M")),
        ).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


# ---------------------------------------------------------------
# RF03 — Consultar disponibilidade (usado por agendar E reagendar)
# ---------------------------------------------------------------
class ActionConsultarDisponibilidade(Action):
    def name(self) -> Text:
        return "action_consultar_disponibilidade"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        especialidade = tracker.get_slot("especialidade")
        intent_atual = tracker.latest_message["intent"].get("name")

        if intent_atual in ("agendarConsulta", "reagendarConsulta"):
            acao = "reagendar" if intent_atual == "reagendarConsulta" else "agendar"
        else:
            # Pedido de continuidade (ex.: "tem outro horário", "um que
            # não seja amanhã") — mantém o que já estava em andamento em
            # vez de assumir "agendar" de novo.
            acao = tracker.get_slot("acao_pendente") or "agendar"

        # Remarcar: com mais de uma consulta futura, o paciente escolhe qual
        # (escolha_consulta_form). Um pedido novo de remarcar sempre refaz a escolha.
        pre: List[Dict[Text, Any]] = []
        if acao == "reagendar":
            ja_escolhida = (tracker.get_slot("acao_consulta") == "escolhida"
                            and intent_atual != "reagendarConsulta")
            if not ja_escolhida:
                futuras = consultas_futuras(resolver_paciente_id(tracker))
                if len(futuras) > 1:
                    return [SlotSet("acao_pendente", "reagendar")] + pedir_escolha_consulta(futuras, "reagendar")
                if len(futuras) == 1:
                    pre.append(SlotSet("consulta_id", futuras[0]["id"]))
                pre.append(SlotSet("acao_consulta", None))

        # Especialidade: para AGENDAR ela é obrigatória. Sem ela, a busca
        # traria médicos de qualquer área; então o bot pergunta (form).
        if especialidade == "__cancelar__":
            dispatcher.utter_message(text="Tudo bem, interrompi o agendamento. Quando quiser, é só pedir de novo.")
            return [SlotSet("especialidade", None), SlotSet("acao_pendente", None)]
        if acao == "agendar":
            canonica = resolver_especialidade(especialidade) if especialidade else None
            if not canonica:
                return [
                    SlotSet("especialidade", None),
                    SlotSet("acao_pendente", "agendar"),
                    SlotSet("horario_id_disponivel", None),
                    ActiveLoop("especialidade_form"),
                ]
            especialidade = canonica

        # Detecta se a data mencionada na mensagem é um filtro POSITIVO
        # ("quero amanhã") ou uma EXCLUSÃO ("não seja amanhã"). Não
        # confiamos na intent para isso (negar_data tem poucos exemplos
        # de treino e quase sempre perde para agendarConsulta, que é
        # muito mais forte) — em vez disso, procuramos marcadores de
        # negação direto no texto, junto da entidade `data` extraída
        # nesta mesma mensagem.
        texto_bruto = tracker.latest_message.get("text") or ""
        texto_normalizado = _remover_acentos(texto_bruto.lower())
        marcadores_negacao = [
            "nao seja", "que nao", " menos ", "exceto", " fora ",
            "diferente de", "sem ser", "tirando", "nao pode ser", "nao quero",
        ]
        eh_negacao = any(m in f" {texto_normalizado} " for m in marcadores_negacao)

        entidades_data = [
            e["value"] for e in tracker.latest_message.get("entities", [])
            if e.get("entity") == "data"
        ]
        data_texto_bruto = entidades_data[0] if entidades_data else None

        data_filtro = None
        data_excluir = None
        if data_texto_bruto:
            if eh_negacao:
                data_excluir = parse_data_relativa(data_texto_bruto)
            else:
                data_filtro = parse_data_relativa(data_texto_bruto)

        # Se o usuário pediu uma nova busca sem confirmar a sugestão
        # anterior, não repetimos os mesmos horários — continuamos
        # excluindo o que já foi oferecido nesta conversa. O reset
        # acontece só depois de confirmar/negar.
        ja_oferecidos = tracker.get_slot("horarios_oferecidos") or []

        conn = get_connection()
        cur = conn.cursor()
        especialidade = normalizar_especialidade(especialidade, cur)
        placeholders = ",".join("?" for _ in ja_oferecidos) if ja_oferecidos else None
        query = """
            SELECT DISTINCT h.id, h.data, h.hora_inicio, m.nome AS medico
            FROM horario h
            JOIN medico m ON m.id = h.medico_id
            JOIN medico_especialidade me ON me.medico_id = m.id
            JOIN especialidade e ON e.id = me.especialidade_id
            WHERE h.status = 'disponivel'
              AND (? IS NULL OR e.nome LIKE '%' || ? || '%')
              AND (? IS NULL OR h.data = ?)
              AND (? IS NULL OR h.data != ?)
              AND (h.data > ? OR (h.data = ? AND h.hora_inicio > ?))
        """
        agora = datetime.now()
        params = [
            especialidade, especialidade,
            data_filtro.isoformat() if data_filtro else None,
            data_filtro.isoformat() if data_filtro else None,
            data_excluir.isoformat() if data_excluir else None,
            data_excluir.isoformat() if data_excluir else None,
            agora.date().isoformat(), agora.date().isoformat(), agora.strftime("%H:%M"),
        ]
        if placeholders:
            query += f" AND h.id NOT IN ({placeholders})"
            params += list(ja_oferecidos)
        query += " ORDER BY h.data, h.hora_inicio LIMIT 3"

        cur.execute(query, params)
        horarios = cur.fetchall()
        conn.close()

        if not horarios:
            dispatcher.utter_message(response="utter_sem_disponibilidade")
            return pre + [
                SlotSet("acao_consulta", None),
                SlotSet("horario_id_disponivel", None),
                SlotSet("acao_pendente", None),
                SlotSet("horarios_oferecidos", []),
                SlotSet("data_excluida", None),
                SlotSet("cancelamento_pendente", None),
            ]

        opcoes = "\n".join(
            f"{i+1}. {h['medico']} — {formatar_data_hora(h['data'], h['hora_inicio'])}"
            for i, h in enumerate(horarios)
        )
        dispatcher.utter_message(text=f"Encontrei estas opções:\n{opcoes}")

        candidatos = [
            {"id": h["id"], "medico": h["medico"], "data": h["data"], "hora_inicio": h["hora_inicio"]}
            for h in horarios
        ]
        novos_ids = ja_oferecidos + [h["id"] for h in horarios]

        return pre + [
            SlotSet("horario_id_disponivel", None),
            SlotSet("acao_pendente", acao),
            SlotSet("horarios_oferecidos", novos_ids),
            SlotSet("horarios_candidatos", candidatos),
            SlotSet("tentativas_escolha", 0.0),
            SlotSet("cancelamento_pendente", None),
            SlotSet("troca_consulta_id", None),
            ActiveLoop("escolha_horario_form"),
        ]


# ---------------------------------------------------------------
# RF04/RF06 — Confirmar o agendamento ou reagendamento pendente
# (só executa depois que o usuário responde "sim")
# ---------------------------------------------------------------
class ActionConfirmarAgendamento(Action):
    def name(self) -> Text:
        return "action_confirmar_agendamento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        pend = cancelamento_pendente_valido(tracker)
        if pend:
            return confirmar_cancelamento(dispatcher, pend)

        acao = tracker.get_slot("acao_pendente")
        horario_id = tracker.get_slot("horario_id_disponivel")
        paciente_id = resolver_paciente_id(tracker)
        # "sim" à pergunta "quer trocar essa consulta?": vira reagendamento
        # da consulta indicada, não um agendamento novo.
        troca_id = tracker.get_slot("troca_consulta_id")
        if acao == "agendar" and troca_id:
            acao = "reagendar"

        # Sempre limpamos o estado pendente ao final, sucesso ou não.
        events: List[Dict[Text, Any]] = [
            SlotSet("horario_id_disponivel", None),
            SlotSet("acao_pendente", None),
            SlotSet("horarios_oferecidos", []),
            SlotSet("data_excluida", None),
            SlotSet("troca_consulta_id", None),
            SlotSet("acao_consulta", None),
        ]
        # Em falha, zera consulta_id para o lembrete (rodado em seguida
        # pela regra) não ser registrado para uma consulta antiga.
        falha = events + [SlotSet("consulta_id", None)]

        if not acao or not horario_id:
            # "confirmo"/"sim" em resposta a um lembrete cai aqui (intent afirmar):
            # sem agendamento pendente, confirma a presença se houver lembrete aguardando.
            if paciente_id and confirmar_presenca_pendente(paciente_id) == "ok":
                dispatcher.utter_message(response="utter_agradecer_confirmacao")
                return falha
            dispatcher.utter_message(text="Não há nenhum agendamento pendente de confirmação no momento.")
            return falha

        if not paciente_id or not consentimento_ativo(paciente_id, FINALIDADE_AGENDAMENTO):
            dispatcher.utter_message(
                text="Para concluir, preciso do seu cadastro e consentimento. "
                     "Peça para agendar novamente que eu cuido disso."
            )
            return falha

        conn = get_connection()
        cur = conn.cursor()
        try:
            if acao == "agendar":
                cur.execute(
                    "UPDATE horario SET status = 'reservado' WHERE id = ? AND status = 'disponivel'",
                    (horario_id,),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    dispatcher.utter_message(text="Esse horário acabou de ser reservado por outra pessoa. Podemos buscar outra opção.")
                    return falha

                cur.execute(
                    "INSERT INTO consulta (paciente_id, horario_id, status) VALUES (?, ?, 'agendada')",
                    (paciente_id, horario_id),
                )
                consulta_id = cur.lastrowid
                conn.commit()
                resposta = "Consulta agendada com sucesso!"

            elif acao == "reagendar":
                consulta_id = None
                if troca_id:
                    ativa = cur.execute(
                        f"SELECT id FROM consulta WHERE id = ? AND paciente_id = ? AND status IN ({_ATIVOS_SQL})",
                        (troca_id, paciente_id),
                    ).fetchone()
                    consulta_id = ativa["id"] if ativa else None
                else:
                    consulta_id = resolver_consulta_ativa(tracker, paciente_id)
                if not consulta_id:
                    dispatcher.utter_message(response="utter_consulta_nao_encontrada")
                    return falha

                antigo = cur.execute(
                    "SELECT horario_id FROM consulta WHERE id = ?", (consulta_id,)
                ).fetchone()

                # Reserva o NOVO horário primeiro, com checagem de que
                # ainda está livre (evita reserva dupla).
                cur.execute(
                    "UPDATE horario SET status = 'reservado' WHERE id = ? AND status = 'disponivel'",
                    (horario_id,),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    dispatcher.utter_message(text="Esse horário acabou de ser reservado por outra pessoa. Podemos buscar outra opção.")
                    return falha

                cur.execute("UPDATE horario SET status = 'disponivel' WHERE id = ?", (antigo["horario_id"],))
                anular_lembretes_pendentes(conn, consulta_id)
                cur.execute(
                    "UPDATE consulta SET horario_id = ?, status = 'reagendada', atualizado_em = ? WHERE id = ?",
                    (horario_id, datetime.now().isoformat(sep=" "), consulta_id),
                )
                conn.commit()
                resposta = "Sua consulta foi reagendada com sucesso!"
            else:
                return falha

            det = buscar_detalhes_consulta(cur, consulta_id)
            detalhe = (
                f"\n{det['medico']} ({det['especialidade']})\n"
                f"{formatar_data_hora(det['data'], det['hora_inicio'])}"
            ) if det else ""
            dispatcher.utter_message(text=resposta + detalhe)
            events.append(SlotSet("consulta_id", consulta_id))

        except sqlite3.Error:
            conn.rollback()
            dispatcher.utter_message(
                text="Tive um problema ao registrar sua consulta. Tente novamente em instantes "
                     "ou peça para falar com um atendente."
            )
            return falha
        finally:
            conn.close()

        return events


# ---------------------------------------------------------------
# Usuário recusou o horário sugerido — descarta a sugestão pendente
# ---------------------------------------------------------------
class ActionNegarAgendamento(Action):
    def name(self) -> Text:
        return "action_negar_agendamento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        if cancelamento_pendente_valido(tracker):
            dispatcher.utter_message(text="Certo, mantive sua consulta como está.")
            return [SlotSet("cancelamento_pendente", None)]

        # "não" à pergunta "quer trocar essa consulta?": oferece marcar o
        # horário escolhido como consulta nova, mantendo a atual. O horário
        # continua pendente; o próximo "sim" agenda e o próximo "não" descarta.
        troca_id = tracker.get_slot("troca_consulta_id")
        horario_id = tracker.get_slot("horario_id_disponivel")
        if troca_id and horario_id:
            conn = get_connection()
            try:
                cur = conn.cursor()
                antiga = buscar_detalhes_consulta(cur, troca_id)
                nova = cur.execute(
                    "SELECT h.data, h.hora_inicio, m.nome AS medico FROM horario h "
                    "JOIN medico m ON m.id = h.medico_id WHERE h.id = ?",
                    (horario_id,),
                ).fetchone()
            finally:
                conn.close()
            if antiga and nova:
                dispatcher.utter_message(text=(
                    f"Certo, não vou trocar. Quer marcar uma nova consulta com {nova['medico']} em "
                    f"{formatar_data_hora(nova['data'], nova['hora_inicio'])}, mantendo a de "
                    f"{formatar_data_hora(antiga['data'], antiga['hora_inicio'])}? Responda sim ou não."
                ))
                return [SlotSet("troca_consulta_id", None)]

        dispatcher.utter_message(text="Tudo bem, não vou reservar esse horário. Se quiser, posso buscar outra opção.")
        return [
            SlotSet("horario_id_disponivel", None),
            SlotSet("acao_pendente", None),
            SlotSet("horarios_oferecidos", []),
            SlotSet("data_excluida", None),
            SlotSet("troca_consulta_id", None),
            SlotSet("acao_consulta", None),
        ]


# ---------------------------------------------------------------
# RF05 — Cancelar consulta existente (com confirmação)
#
# 1) action_pedir_confirmacao_cancelamento: mostra a consulta e pergunta
#    "sim/não", guardando `cancelamento_pendente` ({consulta_id, ts}).
# 2) o "sim" (intent afirmar) cai em action_confirmar_agendamento, que
#    detecta o cancelamento pendente e chama executar_cancelamento().
#    O "não" cai em action_negar_agendamento, que mantém a consulta.
# O pedido expira em CANCELAMENTO_VALIDADE_MIN minutos, para um "sim"
# solto, muito depois, não cancelar nada por engano.
# ---------------------------------------------------------------
CANCELAMENTO_VALIDADE_MIN = 5


def cancelamento_pendente_valido(tracker: Tracker) -> Optional[Dict[str, Any]]:
    pend = tracker.get_slot("cancelamento_pendente")
    if not isinstance(pend, dict) or not pend.get("consulta_id"):
        return None
    try:
        criado = datetime.fromisoformat(pend["ts"])
    except (KeyError, ValueError):
        return None
    if datetime.now() - criado > timedelta(minutes=CANCELAMENTO_VALIDADE_MIN):
        return None
    return pend


def executar_cancelamento(consulta_id: int) -> str:
    """Cancela a consulta e libera o horário. Retorna 'ok',
    'nao_encontrada' ou 'erro'."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT horario_id FROM consulta WHERE id = ? AND status IN ({_ATIVOS_SQL})",
            (consulta_id,),
        ).fetchone()
        if not row:
            return "nao_encontrada"
        conn.execute(
            "UPDATE consulta SET status = 'cancelada', atualizado_em = ? WHERE id = ?",
            (datetime.now().isoformat(sep=" "), consulta_id),
        )
        conn.execute("UPDATE horario SET status = 'disponivel' WHERE id = ?", (row["horario_id"],))
        anular_lembretes_pendentes(conn, consulta_id)
        conn.commit()
        return "ok"
    except sqlite3.Error:
        conn.rollback()
        return "erro"
    finally:
        conn.close()


def confirmar_cancelamento(dispatcher: CollectingDispatcher, pend: Dict[str, Any]) -> List[Dict[Text, Any]]:
    resultado = executar_cancelamento(pend["consulta_id"])
    if resultado == "ok":
        dispatcher.utter_message(response="utter_cancelamento_confirmado")
    elif resultado == "nao_encontrada":
        dispatcher.utter_message(response="utter_consulta_nao_encontrada")
    else:
        dispatcher.utter_message(text="Não consegui cancelar agora. Tente novamente ou peça para falar com um atendente.")
    # limpa o pedido e o consulta_id: a consulta cancelada não deve ser reutilizada
    return [SlotSet("cancelamento_pendente", None), SlotSet("consulta_id", None)]


class ActionPedirConfirmacaoCancelamento(Action):
    def name(self) -> Text:
        return "action_pedir_confirmacao_cancelamento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        paciente_id = resolver_paciente_id(tracker)
        # Com mais de uma consulta futura, o paciente escolhe qual cancelar;
        # com uma, é essa; sem nenhuma futura, mantém o critério anterior.
        futuras = consultas_futuras(paciente_id)
        if len(futuras) > 1:
            return [SlotSet("acao_pendente", None)] + pedir_escolha_consulta(futuras, "cancelar")
        if futuras:
            consulta_id = futuras[0]["id"]
        else:
            consulta_id = resolver_consulta_ativa(tracker, paciente_id) if paciente_id else None
        return pedir_confirmacao_cancelamento(dispatcher, consulta_id)


def pedir_confirmacao_cancelamento(dispatcher: CollectingDispatcher,
                                   consulta_id: Optional[int]) -> List[Dict[Text, Any]]:
    if not consulta_id:
        dispatcher.utter_message(response="utter_consulta_nao_encontrada")
        return [SlotSet("cancelamento_pendente", None)]

    conn = get_connection()
    try:
        det = buscar_detalhes_consulta(conn.cursor(), consulta_id)
    finally:
        conn.close()

    dispatcher.utter_message(text=(
        f"Você quer cancelar a consulta com {det['medico']} ({det['especialidade']}) "
        f"em {formatar_data_hora(det['data'], det['hora_inicio'])}? Responda sim ou não."
    ))
    return [
        SlotSet("cancelamento_pendente", {"consulta_id": consulta_id, "ts": datetime.now().isoformat()}),
        # só um pedido de confirmação por vez: descarta reserva pendente
        SlotSet("horario_id_disponivel", None),
        SlotSet("acao_pendente", None),
        SlotSet("troca_consulta_id", None),
    ]


# ---------------------------------------------------------------
# Consultar o próprio agendamento ("qual data eu agendei?")
# ---------------------------------------------------------------
class ActionConsultarAgendamento(Action):
    def name(self) -> Text:
        return "action_consultar_agendamento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        paciente_id = resolver_paciente_id(tracker)
        agora = datetime.now()
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""
                SELECT h.data, h.hora_inicio, m.nome AS medico,
                       (SELECT e.nome FROM medico_especialidade me
                          JOIN especialidade e ON e.id = me.especialidade_id
                         WHERE me.medico_id = m.id
                         ORDER BY me.principal DESC LIMIT 1) AS especialidade
                FROM consulta c
                JOIN horario h ON h.id = c.horario_id
                JOIN medico m ON m.id = h.medico_id
                WHERE c.paciente_id = ? AND c.status IN ({_ATIVOS_SQL})
                  AND (h.data > ? OR (h.data = ? AND h.hora_inicio > ?))
                ORDER BY h.data, h.hora_inicio LIMIT 5
                """,
                (paciente_id, agora.date().isoformat(), agora.date().isoformat(),
                 agora.strftime("%H:%M")),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            dispatcher.utter_message(response="utter_consulta_nao_encontrada")
        elif len(rows) == 1:
            r = rows[0]
            dispatcher.utter_message(text=(
                f"Sua consulta está marcada com {r['medico']} ({r['especialidade']}) "
                f"para {formatar_data_hora(r['data'], r['hora_inicio'])}."
            ))
        else:
            linhas = "\n".join(
                f"{i+1}. {r['medico']} ({r['especialidade']}) — "
                f"{formatar_data_hora(r['data'], r['hora_inicio'])}"
                for i, r in enumerate(rows)
            )
            dispatcher.utter_message(text=f"Suas próximas consultas:\n{linhas}")
        return []


# ---------------------------------------------------------------
# RF07 — Informar convênios aceitos
# ---------------------------------------------------------------
class ActionInformarConvenio(Action):
    def name(self) -> Text:
        return "action_informar_convenio"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        convenio_perguntado = tracker.get_slot("convenio")
        conn = get_connection()
        cur = conn.cursor()

        if convenio_perguntado:
            cur.execute("SELECT nome, ativo FROM convenio WHERE nome LIKE '%' || ? || '%'", (convenio_perguntado,))
            row = cur.fetchone()
            if row and row["ativo"]:
                dispatcher.utter_message(text=f"Sim, atendemos pelo convênio {row['nome']}.")
            elif row:
                dispatcher.utter_message(text=f"O convênio {row['nome']} não está mais ativo em nossa clínica.")
            else:
                dispatcher.utter_message(text="Não localizei esse convênio em nossa base. Posso verificar com a recepção.")
        else:
            cur.execute("SELECT nome FROM convenio WHERE ativo = 1")
            nomes = ", ".join(r["nome"] for r in cur.fetchall())
            dispatcher.utter_message(text=f"Aceitamos os seguintes convênios: {nomes}.")

        conn.close()
        return []


# ---------------------------------------------------------------
# RF08 — Registrar lembrete ao confirmar um agendamento/reagendamento.
#
# O lembrete é programado para LEMBRETE_ANTECEDENCIA_H horas antes da
# consulta (ou "agora", se a consulta já estiver mais perto que isso)
# e só é criado se o paciente consentiu com essa finalidade (LGPD).
# O envio de fato é feito pelo processo `enviar_lembretes.py`.
# ---------------------------------------------------------------
class ActionRegistrarLembrete(Action):
    def name(self) -> Text:
        return "action_registrar_lembrete"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        registrar_lembrete(tracker.get_slot("consulta_id"), resolver_paciente_id(tracker))
        return []


def registrar_lembrete(consulta_id: Optional[int], paciente_id: Optional[int]) -> None:
    """Programa o lembrete da consulta (24 h antes, ou em 1 min se faltar
    menos que isso), se o paciente consentiu com lembretes."""
    if not consulta_id or not paciente_id:
        return  # nenhuma consulta registrada nesta rodada
    if not consentimento_ativo(paciente_id, FINALIDADE_LEMBRETES):
        return  # sem consentimento para lembretes: não agenda nenhum

    conn = get_connection()
    try:
        det = buscar_detalhes_consulta(conn.cursor(), consulta_id)
        if not det:
            return
        quando = datetime.fromisoformat(f"{det['data']} {det['hora_inicio']}")
        agora = datetime.now()
        envio = max(quando - timedelta(hours=LEMBRETE_ANTECEDENCIA_H),
                    agora + timedelta(minutes=1))
        anular_lembretes_pendentes(conn, consulta_id)  # evita duplicar no reagendamento
        conn.execute(
            "INSERT INTO lembrete (consulta_id, data_envio, status_confirmacao) VALUES (?, ?, 'pendente')",
            (consulta_id, envio.isoformat(sep=" ", timespec="seconds")),
        )
        conn.commit()
    except (sqlite3.Error, ValueError):
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------
# RF13 — Registrar log de interação (auditoria / avaliação do PLN)
# ---------------------------------------------------------------
class ActionLogInteracao(Action):
    def name(self) -> Text:
        return "action_log_interacao"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        intent_data = tracker.latest_message.get("intent", {})
        paciente_id = resolver_paciente_id(tracker)  # None se ainda não cadastrado

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO log_interacao (paciente_id, intent_detectado, confianca, canal) VALUES (?, ?, ?, 'chatbot')",
            (paciente_id, intent_data.get("name", "desconhecido"), intent_data.get("confidence", 0.0)),
        )
        conn.commit()
        conn.close()

        # Qualquer outra intenção entre o pedido e a resposta cancela o pedido.
        if (tracker.get_slot("cancelamento_pendente")
                and intent_data.get("name") not in ("afirmar", "negar", "cancelarConsulta")):
            return [SlotSet("cancelamento_pendente", None)]
        return []


# ---------------------------------------------------------------
# RF12 — Escalonamento para atendente humano
#
# Registra um chamado na tabela `handoff` (fonte de verdade para o
# canal/painel saber que a conversa está com um humano) e marca o slot
# `atendimento_humano`. O Rasa não "silencia" sozinho: quem deve parar
# de encaminhar mensagens ao bot enquanto houver chamado aberto é a
# camada do canal (ex.: o conector do Telegram), consultando `handoff`.
# ---------------------------------------------------------------
class ActionEncaminharAtendimentoHumano(Action):
    def name(self) -> Text:
        return "action_encaminhar_atendimento_humano"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        motivo = (tracker.latest_message.get("intent") or {}).get("name", "desconhecido")
        ja_em_fila = False
        try:
            conn = get_connection()
            ja_em_fila = conn.execute(
                "SELECT 1 FROM handoff WHERE sender_id = ? AND status IN ('aguardando','em_atendimento')",
                (tracker.sender_id,),
            ).fetchone() is not None
            if not ja_em_fila:
                conn.execute(
                    "INSERT INTO handoff (sender_id, motivo) VALUES (?, ?)",
                    (tracker.sender_id, motivo),
                )
                conn.commit()
            conn.close()
        except sqlite3.Error:
            pass  # o aviso ao paciente não pode falhar por causa do registro

        if ja_em_fila:
            dispatcher.utter_message(text="Você já está na fila de atendimento. Aguarde, por favor.")
        else:
            dispatcher.utter_message(response="utter_encaminhar_humano")

        return [
            SlotSet("atendimento_humano", True),
            SlotSet("horario_id_disponivel", None),
            SlotSet("acao_pendente", None),
            SlotSet("cancelamento_pendente", None),
        ]


# ---------------------------------------------------------------
# Form: faz o PACIENTE escolher qual horário quer, entre os listados
# ---------------------------------------------------------------
class ValidateEscolhaHorarioForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_escolha_horario_form"

    def validate_horario_escolhido(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:

        candidatos = tracker.get_slot("horarios_candidatos") or []
        resultado = casar_selecao_horario(str(slot_value), candidatos)

        if resultado is None:
            tentativas = (tracker.get_slot("tentativas_escolha") or 0) + 1
            if tentativas >= 3:
                # 3 respostas seguidas sem entender: sai do form em vez de
                # prender o paciente num laço.
                return {"horario_escolhido": "CANCELAR", "tentativas_escolha": 0.0}
            opcoes = "\n".join(
                f"{i+1}. {c['medico']} — {formatar_data_hora(c['data'], c['hora_inicio'])}"
                for i, c in enumerate(candidatos)
            )
            dispatcher.utter_message(
                text=(
                    "Não consegui identificar qual horário você quer. Responda com "
                    "o número da opção, o horário (ex.: 08:30) ou o nome do médico:\n" + opcoes
                )
            )
            return {"horario_escolhido": None, "tentativas_escolha": float(tentativas)}

        return {"horario_escolhido": str(resultado), "tentativas_escolha": 0.0}


class ActionSelecionarHorario(Action):
    """Ação de submissão do escolha_horario_form: lê o que foi
    identificado em horario_escolhido e prepara a confirmação final
    (a reserva de fato só acontece em ActionConfirmarAgendamento,
    disparada pela intent `afirmar` na próxima mensagem)."""

    def name(self) -> Text:
        return "action_selecionar_horario"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        valor = tracker.get_slot("horario_escolhido")
        candidatos = tracker.get_slot("horarios_candidatos") or []
        events: List[Dict[Text, Any]] = [SlotSet("horario_escolhido", None), SlotSet("troca_consulta_id", None)]

        if valor == "MAIS":
            # Mostra as próximas opções; horarios_oferecidos evita repetir as já listadas.
            return events + [FollowupAction("action_consultar_disponibilidade")]

        if valor == "CANCELAR_CONSULTA":
            paciente_id = resolver_paciente_id(tracker)
            if paciente_id and find_active_consulta_id(paciente_id):
                # Chama a ação direto (não por FollowupAction): assim a regra
                # "processar escolha de horario" termina aqui e o próximo
                # "sim"/"não" cai no fluxo normal de confirmação.
                return events + [
                    SlotSet("horarios_candidatos", []),
                    SlotSet("horarios_oferecidos", []),
                ] + ActionPedirConfirmacaoCancelamento().run(dispatcher, tracker, domain)
            valor = "CANCELAR"  # sem consulta ativa: só encerra a escolha

        if valor == "CANCELAR":
            dispatcher.utter_message(
                text="Tudo bem, não vou reservar nada. Se quiser, peça para buscar outros "
                     "horários (ex.: 'tem outro horário disponível?') ou informe uma data específica."
            )
            events += [
                SlotSet("horarios_candidatos", []),
                SlotSet("horario_id_disponivel", None),
            ]
            return events

        candidato = next((c for c in candidatos if str(c["id"]) == str(valor)), None)
        if not candidato:
            dispatcher.utter_message(text="Algo deu errado ao identificar o horário escolhido. Pode tentar de novo?")
            return events

        novo = f"{candidato['medico']} — {formatar_data_hora(candidato['data'], candidato['hora_inicio'])}"

        # Pedido de "agendar" de quem já tem consulta futura: pode ser uma
        # troca de horário dita sem "remarcar" (ex.: "tem como marcar pra
        # 15:30"). Pergunta antes de criar uma segunda consulta.
        if tracker.get_slot("acao_pendente") == "agendar":
            troca_id = consulta_ativa_futura(resolver_paciente_id(tracker))
            if troca_id:
                conn = get_connection()
                try:
                    det = buscar_detalhes_consulta(conn.cursor(), troca_id)
                finally:
                    conn.close()
                dispatcher.utter_message(text=(
                    f"Você já tem consulta com {det['medico']} ({det['especialidade']}) em "
                    f"{formatar_data_hora(det['data'], det['hora_inicio'])}. Quer trocar essa "
                    f"consulta por {novo}? Responda sim ou não."
                ))
                events += [SlotSet("horario_id_disponivel", candidato["id"]),
                           SlotSet("troca_consulta_id", troca_id)]
                return events

        dispatcher.utter_message(text=f"Você escolheu: {novo}. Confirma o agendamento?")
        events.append(SlotSet("horario_id_disponivel", candidato["id"]))
        return events

# ---------------------------------------------------------------
# Form: paciente com mais de uma consulta futura escolhe qual cancelar
# ou remarcar (parecer de orientação, item A2)
# ---------------------------------------------------------------
VERBO_ACAO_CONSULTA = {"cancelar": "cancelar", "reagendar": "remarcar"}
RECUSA_ESCOLHA = ("nao", "n", "nenhuma", "nenhum", "nenhuma delas", "nenhuma dessas", "deixa", "deixa pra la")


class ActionAskConsultaEscolhida(Action):
    def name(self) -> Text:
        return "action_ask_consulta_escolhida"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cands = tracker.get_slot("consultas_candidatas") or []
        verbo = VERBO_ACAO_CONSULTA.get(tracker.get_slot("acao_consulta"), "alterar")
        dispatcher.utter_message(text=(
            f"Você tem mais de uma consulta marcada:\n{listar_consultas(cands)}\n"
            f"Qual você quer {verbo}? Responda com o número, o horário ou o nome do médico."
        ))
        return []


class ValidateEscolhaConsultaForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_escolha_consulta_form"

    def validate_consulta_escolhida(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # Na abertura do form, a mensagem é o próprio pedido ("quero cancelar
        # minha consulta"), não a escolha: deixa o form fazer a pergunta.
        if tracker.get_slot("requested_slot") != "consulta_escolhida":
            return {"consulta_escolhida": None}

        cands = tracker.get_slot("consultas_candidatas") or []
        t = _remover_acentos(str(slot_value).strip().lower()).strip(" .!")
        if t in RECUSA_ESCOLHA:
            return {"consulta_escolhida": "CANCELAR", "tentativas_escolha": 0.0}

        resultado = casar_selecao_horario(str(slot_value), cands, marcadores=False)
        if resultado is None:
            tentativas = (tracker.get_slot("tentativas_escolha") or 0) + 1
            if tentativas >= 3:
                return {"consulta_escolhida": "CANCELAR", "tentativas_escolha": 0.0}
            dispatcher.utter_message(text=(
                "Não consegui identificar qual consulta. Responda com o número da opção, "
                "o horário (ex.: 08:30) ou o nome do médico:\n" + listar_consultas(cands)
            ))
            return {"consulta_escolhida": None, "tentativas_escolha": float(tentativas)}

        return {"consulta_escolhida": str(resultado), "tentativas_escolha": 0.0}


class ActionSelecionarConsulta(Action):
    """Submissão do escolha_consulta_form: segue para a confirmação do
    cancelamento ou para a busca de horários da consulta escolhida."""

    def name(self) -> Text:
        return "action_selecionar_consulta"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        valor = tracker.get_slot("consulta_escolhida")
        acao = tracker.get_slot("acao_consulta")
        cands = tracker.get_slot("consultas_candidatas") or []
        escolhida = next((c for c in cands if str(c["id"]) == str(valor)), None)
        events: List[Dict[Text, Any]] = [
            SlotSet("consulta_escolhida", None),
            SlotSet("consultas_candidatas", []),
            SlotSet("tentativas_escolha", 0.0),
        ]

        if not escolhida or acao not in VERBO_ACAO_CONSULTA:
            dispatcher.utter_message(text="Tudo bem, não alterei nenhuma consulta.")
            return events + [SlotSet("acao_consulta", None), SlotSet("acao_pendente", None)]

        if acao == "cancelar":
            return events + [SlotSet("acao_consulta", None)] + pedir_confirmacao_cancelamento(
                dispatcher, escolhida["id"])

        # remarcar: guarda a consulta escolhida e mostra os horários livres
        return events + [
            SlotSet("consulta_id", escolhida["id"]),
            SlotSet("acao_consulta", "escolhida"),
            SlotSet("acao_pendente", "reagendar"),
            SlotSet("horarios_oferecidos", []),
            FollowupAction("action_consultar_disponibilidade"),
        ]

# =====================================================================
# LGPD (RF11), CADASTRO DE PACIENTE (RF10) E LEMBRETES (RF08/RF09)
# =====================================================================
# Constantes de finalidade: ficam gravadas em consentimento_lgpd.finalidade
# e também são usadas por enviar_lembretes.py (não altere o texto depois
# de haver registros, ou os consentimentos antigos deixam de casar).
FINALIDADE_AGENDAMENTO = "agendamento de consultas e atendimento administrativo"
FINALIDADE_LEMBRETES = "envio de lembretes e confirmação de presença"
BASE_LEGAL_CONSENTIMENTO = "consentimento do titular (LGPD, art. 7º, I, e art. 11, I)"
LEMBRETE_ANTECEDENCIA_H = 24

SLOTS_CADASTRO = [
    "consent_agendamento", "consent_lembretes", "consent_ts",
    "cadastro_nome", "cadastro_nascimento", "cadastro_telefone", "cadastro_convenio",
]
SLOTS_CONSENTIMENTO = ("consent_agendamento", "consent_lembretes")


# ---------------- acesso ao banco ----------------
def buscar_paciente_por_chat(chat_id: Any) -> Optional[sqlite3.Row]:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT id, nome, telefone, data_nascimento FROM paciente WHERE telegram_chat_id = ?",
            (str(chat_id),),
        ).fetchone()
    finally:
        conn.close()


def consentimento_ativo(paciente_id: Optional[int], finalidade: str) -> bool:
    if not paciente_id:
        return False
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM consentimento_lgpd "
            "WHERE paciente_id = ? AND finalidade = ? AND revogado_em IS NULL",
            (paciente_id, finalidade),
        ).fetchone() is not None
    finally:
        conn.close()


def anular_lembretes_pendentes(conn: sqlite3.Connection, consulta_id: int) -> None:
    """Marca como 'ignorado' os lembretes ainda pendentes de uma consulta
    (cancelada, reagendada ou sem consentimento). Não faz commit."""
    conn.execute(
        "UPDATE lembrete SET status_confirmacao = 'ignorado' "
        "WHERE consulta_id = ? AND status_confirmacao = 'pendente'",
        (consulta_id,),
    )


def _agora_iso() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


# ---------------- interpretação das respostas ----------------
_AFIRMATIVOS = {"sim", "s", "aceito", "concordo", "autorizo", "ok", "pode", "claro", "yes", "positivo"}
_NEGATIVOS = {"nao", "n", "recuso", "nego", "negativo", "nunca"}
_SAIR = {"cancelar", "cancela", "desistir", "desisto", "sair", "parar"}
_MANTER = {"manter", "mesmo", "igual", "pular", "mantenho"}


def _palavras(texto: str) -> List[str]:
    t = _remover_acentos(str(texto).strip().lower())
    return re.sub(r"[^\w\s]", " ", t).split()


def interpretar_sim_nao(texto: str) -> Optional[bool]:
    palavras = _palavras(texto)
    if any(p in _NEGATIVOS for p in palavras):
        return False
    if any(p in _AFIRMATIVOS for p in palavras):
        return True
    return None


def quer_sair(texto: str) -> bool:
    return any(p in _SAIR for p in _palavras(texto))


def parse_data_nascimento(texto: str) -> Optional[date]:
    """Aceita dd/mm/aaaa, dd-mm-aaaa, dd.mm.aaaa, 'dd mm aaaa' e ddmmaaaa
    (ano sempre com 4 dígitos, para não adivinhar o século)."""
    t = str(texto).strip()
    m = re.search(r"(\d{1,2})\s*[/\-. ]\s*(\d{1,2})\s*[/\-. ]\s*(\d{4})", t)
    if not m and re.fullmatch(r"\d{8}", t):  # ddmmaaaa
        m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", t)
    if not m:
        return None
    dia, mes, ano = (int(x) for x in m.groups())
    try:
        d = date(ano, mes, dia)
    except ValueError:
        return None
    hoje = date.today()
    if d > hoje or d.year < hoje.year - 120:
        return None
    return d


def normalizar_telefone(texto: str) -> Optional[str]:
    """Devolve só os dígitos (DDD + número) ou None se não parecer um
    telefone brasileiro."""
    digitos = re.sub(r"\D", "", str(texto))
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos if len(digitos) in (10, 11) else None


def resolver_convenio_texto(texto: str) -> Optional[str]:
    """'particular'/'não tenho' -> 'Particular'; nome de convênio ativo
    (com tolerância a erro de digitação) -> nome oficial; senão None."""
    t = _remover_acentos(str(texto).strip().lower())
    if "particular" in t or t.startswith("nao") or t in ("nenhum", "sem", "sem convenio"):
        return "Particular"
    conn = get_connection()
    try:
        nomes = [r["nome"] for r in conn.execute("SELECT nome FROM convenio WHERE ativo = 1")]
    finally:
        conn.close()
    por_norm = {_remover_acentos(n.lower()): n for n in nomes}
    for norm, nome in por_norm.items():
        if norm in t:
            return nome
    parecidos = difflib.get_close_matches(t, list(por_norm), n=1, cutoff=0.6)
    return por_norm[parecidos[0]] if parecidos else None


def _convenio_atual(conn: sqlite3.Connection, paciente_id: int) -> str:
    row = conn.execute(
        "SELECT c.nome FROM paciente_convenio pc JOIN convenio c ON c.id = pc.convenio_id "
        "WHERE pc.paciente_id = ? ORDER BY pc.principal DESC LIMIT 1",
        (paciente_id,),
    ).fetchone()
    return row["nome"] if row else "Particular"


def _limpar_cadastro() -> List[Dict[Text, Any]]:
    return ([SlotSet(s, None) for s in SLOTS_CADASTRO]
            + [SlotSet("cadastro_cancelado", False),
               SlotSet("modo_cadastro", None),
               SlotSet("dados_atuais", None)])


def listar_especialidades() -> List[str]:
    conn = get_connection()
    try:
        return [r["nome"] for r in conn.execute("SELECT nome FROM especialidade ORDER BY nome")]
    finally:
        conn.close()


def resolver_especialidade(texto: Optional[str]) -> Optional[str]:
    """Associa o que a pessoa escreveu ('cardiologista', 'cardio', 'coração')
    a uma especialidade cadastrada. Devolve o nome oficial ou None."""
    if not texto:
        return None
    t = _remover_acentos(str(texto).strip().lower())
    if not t:
        return None
    por_norm = {_remover_acentos(n.lower()): n for n in listar_especialidades()}
    for norm, nome in por_norm.items():
        if norm in t or t in norm and len(t) >= 4:
            return nome
    parecidos = difflib.get_close_matches(t, list(por_norm), n=1, cutoff=0.6)
    return por_norm[parecidos[0]] if parecidos else None


class ActionAskEspecialidade(Action):
    """Pergunta da especialidade, listando as que existem no banco."""

    def name(self) -> Text:
        return "action_ask_especialidade"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        nomes = listar_especialidades()
        lista = f" Temos: {', '.join(nomes)}." if nomes else ""
        dispatcher.utter_message(text=f"Para qual especialidade você quer agendar a consulta?{lista}")
        return []


class ValidateEspecialidadeForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_especialidade_form"

    def validate_especialidade(self, slot_value: Any, dispatcher: CollectingDispatcher,
                               tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value or "")
        if quer_sair(texto):
            return {"especialidade": "__cancelar__"}  # tratado em action_consultar_disponibilidade
        nome = resolver_especialidade(texto)
        if not nome:
            nomes = ", ".join(listar_especialidades())
            dispatcher.utter_message(
                text=f"Não encontrei essa especialidade. Temos: {nomes}. Qual delas você prefere? (ou \"cancelar\")"
            )
            return {"especialidade": None}
        return {"especialidade": nome}


# ---------------------------------------------------------------
# Porta de entrada do agendamento: sem cadastro + consentimento ativo,
# o paciente passa primeiro pelo cadastro_form (RF10/RF11).
# ---------------------------------------------------------------
class ActionVerificarCadastro(Action):
    def name(self) -> Text:
        return "action_verificar_cadastro"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        intent_atual = (tracker.latest_message.get("intent") or {}).get("name")
        if intent_atual == "reagendarConsulta":
            acao = "reagendar"
        elif intent_atual == "agendarConsulta":
            acao = "agendar"
        else:
            acao = tracker.get_slot("acao_pendente") or "agendar"

        # Pedido NOVO de agendamento sem especialidade na frase: esquece a da
        # conversa anterior (o slot sobrevive entre pedidos) para o bot perguntar.
        esp_na_frase = any(e.get("entity") == "especialidade"
                           for e in tracker.latest_message.get("entities", []))
        esquecer_esp = ([SlotSet("especialidade", None)]
                        if intent_atual == "agendarConsulta" and not esp_na_frase else [])

        pac = buscar_paciente_por_chat(tracker.sender_id)
        if pac and consentimento_ativo(pac["id"], FINALIDADE_AGENDAMENTO):
            # Já cadastrado e com consentimento: segue direto para os horários.
            # (a ação seguinte relê a mesma mensagem: intenção e entidades.)
            return [SlotSet("paciente_id", pac["id"]),
                    SlotSet("acao_pendente", acao),
                    *esquecer_esp,
                    FollowupAction("action_consultar_disponibilidade")]

        events: List[Dict[Text, Any]] = esquecer_esp + _limpar_cadastro() + [
            SlotSet("acao_pendente", acao),
            SlotSet("modo_cadastro", "cadastro"),
            SlotSet("horario_id_disponivel", None),
            SlotSet("cancelamento_pendente", None),
        ]
        if pac:
            # Cadastro existe, mas o consentimento foi revogado: reaproveita
            # os dados já gravados e só pede o consentimento de novo.
            conn = get_connection()
            try:
                convenio = _convenio_atual(conn, pac["id"])
            finally:
                conn.close()
            events += [
                SlotSet("cadastro_nome", pac["nome"]),
                SlotSet("cadastro_nascimento", pac["data_nascimento"]),
                SlotSet("cadastro_telefone", pac["telefone"]),
                SlotSet("cadastro_convenio", convenio),
            ]
            dispatcher.utter_message(
                text="Para continuar, preciso que você autorize novamente o uso dos seus dados."
            )
        else:
            dispatcher.utter_message(response="utter_aviso_cadastro")
        events.append(ActiveLoop("cadastro_form"))
        return events


class ActionAtualizarCadastro(Action):
    """Inicia a atualização de dados (RF10). Reaproveita o cadastro_form
    sem perguntar o consentimento de novo; 'manter' preserva o valor atual."""

    def name(self) -> Text:
        return "action_atualizar_cadastro"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        pac = buscar_paciente_por_chat(tracker.sender_id)
        if not pac:
            dispatcher.utter_message(
                text="Você ainda não tem cadastro. Ele é feito automaticamente quando você pede para agendar uma consulta."
            )
            return []
        if not consentimento_ativo(pac["id"], FINALIDADE_AGENDAMENTO):
            dispatcher.utter_message(
                text="Seu consentimento está revogado, então não posso alterar seus dados por aqui. "
                     "Peça para agendar uma consulta para autorizar novamente, ou fale com a recepção."
            )
            return []

        conn = get_connection()
        try:
            convenio = _convenio_atual(conn, pac["id"])
        finally:
            conn.close()
        atuais = {"nome": pac["nome"], "data_nascimento": pac["data_nascimento"],
                  "telefone": pac["telefone"], "convenio": convenio}
        try:
            nasc = date.fromisoformat(pac["data_nascimento"]).strftime("%d/%m/%Y")
        except ValueError:
            nasc = pac["data_nascimento"]
        dispatcher.utter_message(text=(
            f"Seus dados atuais:\nNome: {pac['nome']}\nNascimento: {nasc}\n"
            f"Telefone: {pac['telefone']}\nConvênio: {convenio}\n\n"
            "Vou perguntar cada um. Responda \"manter\" para não alterar, ou \"cancelar\" para parar."
        ))
        return _limpar_cadastro() + [
            SlotSet("modo_cadastro", "atualizacao"),
            SlotSet("dados_atuais", atuais),
            SlotSet("paciente_id", pac["id"]),
            ActiveLoop("cadastro_form"),
        ]


# ---------------------------------------------------------------
# Form: consentimento (antes de qualquer dado) + dados mínimos
# ---------------------------------------------------------------
class ValidateCadastroForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_cadastro_form"

    async def required_slots(self, domain_slots: List[Text], dispatcher: CollectingDispatcher,
                             tracker: Tracker, domain: Dict[Text, Any]) -> List[Text]:
        if tracker.get_slot("cadastro_cancelado"):
            return []  # recusa ou desistência: encerra o form sem pedir mais nada
        if tracker.get_slot("modo_cadastro") == "atualizacao":
            return [s for s in domain_slots if s not in SLOTS_CONSENTIMENTO]
        return list(domain_slots)

    # --- utilitários ---
    @staticmethod
    def _manter(tracker: Tracker, campo: str, texto: str) -> Optional[str]:
        if tracker.get_slot("modo_cadastro") != "atualizacao":
            return None
        if any(p in _MANTER for p in _palavras(texto)):
            return (tracker.get_slot("dados_atuais") or {}).get(campo)
        return None

    @staticmethod
    def _desistir(slot: str) -> Dict[Text, Any]:
        return {slot: None, "cadastro_cancelado": True}

    # --- consentimentos ---
    def validate_consent_agendamento(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                     tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("consent_agendamento")
        resposta = interpretar_sim_nao(texto)
        if resposta is None:
            dispatcher.utter_message(text="Não entendi. Você autoriza o uso dos seus dados para agendamento? Responda SIM ou NÃO.")
            return {"consent_agendamento": None}
        if resposta is False:
            return {"consent_agendamento": False, "cadastro_cancelado": True}
        return {"consent_agendamento": True, "consent_ts": _agora_iso()}

    def validate_consent_lembretes(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                   tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("consent_lembretes")
        resposta = interpretar_sim_nao(texto)
        if resposta is None:
            dispatcher.utter_message(text="Não entendi. Você quer receber lembretes? Responda SIM ou NÃO.")
            return {"consent_lembretes": None}
        return {"consent_lembretes": resposta}

    # --- dados do paciente ---
    def validate_cadastro_nome(self, slot_value: Any, dispatcher: CollectingDispatcher,
                               tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("cadastro_nome")
        mantido = self._manter(tracker, "nome", texto)
        if mantido:
            return {"cadastro_nome": mantido}
        nome = re.sub(r"\s+", " ", texto.strip())
        if len(nome) < 5 or len(nome) > 100 or " " not in nome or re.search(r"\d", nome):
            dispatcher.utter_message(text="Preciso do nome completo (nome e sobrenome), sem números.")
            return {"cadastro_nome": None}
        return {"cadastro_nome": nome}

    def validate_cadastro_nascimento(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                     tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("cadastro_nascimento")
        mantido = self._manter(tracker, "data_nascimento", texto)
        if mantido:
            return {"cadastro_nascimento": mantido}
        d = parse_data_nascimento(texto)
        if not d:
            dispatcher.utter_message(text="Não consegui entender a data. Use o formato dd/mm/aaaa, por exemplo 25/03/1990.")
            return {"cadastro_nascimento": None}
        return {"cadastro_nascimento": d.isoformat()}

    def validate_cadastro_telefone(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                   tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("cadastro_telefone")
        mantido = self._manter(tracker, "telefone", texto)
        if mantido:
            return {"cadastro_telefone": mantido}
        tel = normalizar_telefone(texto)
        if not tel:
            dispatcher.utter_message(text="Telefone inválido. Informe com DDD, por exemplo (45) 99999-8888.")
            return {"cadastro_telefone": None}
        return {"cadastro_telefone": tel}

    def validate_cadastro_convenio(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                   tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        texto = str(slot_value)
        if quer_sair(texto):
            return self._desistir("cadastro_convenio")
        mantido = self._manter(tracker, "convenio", texto)
        if mantido:
            return {"cadastro_convenio": mantido}
        convenio = resolver_convenio_texto(texto)
        if not convenio:
            conn = get_connection()
            try:
                nomes = ", ".join(r["nome"] for r in conn.execute("SELECT nome FROM convenio WHERE ativo = 1"))
            finally:
                conn.close()
            dispatcher.utter_message(
                text=f"Não reconheci esse convênio. Aceitamos: {nomes}. Se não tiver convênio, responda \"particular\"."
            )
            return {"cadastro_convenio": None}
        return {"cadastro_convenio": convenio}


class ActionFinalizarCadastro(Action):
    """Submissão do cadastro_form: grava paciente, convênio e os registros
    de consentimento (RF10/RF11) e retoma o agendamento que estava pendente."""

    def name(self) -> Text:
        return "action_finalizar_cadastro"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        limpar = _limpar_cadastro()
        modo = tracker.get_slot("modo_cadastro") or "cadastro"

        if tracker.get_slot("cadastro_cancelado"):
            if tracker.get_slot("consent_agendamento") is False:
                dispatcher.utter_message(response="utter_sem_consentimento")
            else:
                dispatcher.utter_message(text="Tudo bem, interrompi o processo. Quando quiser, é só pedir de novo.")
            return limpar + [SlotSet("acao_pendente", None)]

        nome = tracker.get_slot("cadastro_nome")
        nasc = tracker.get_slot("cadastro_nascimento")
        tel = tracker.get_slot("cadastro_telefone")
        convenio = tracker.get_slot("cadastro_convenio")
        if not (nome and nasc and tel):
            dispatcher.utter_message(text="Faltaram dados no cadastro. Podemos tentar de novo quando quiser.")
            return limpar + [SlotSet("acao_pendente", None)]

        chat_id = str(tracker.sender_id)
        agora = _agora_iso()
        conn = get_connection()
        try:
            row = conn.execute("SELECT id FROM paciente WHERE telegram_chat_id = ?", (chat_id,)).fetchone()
            if row:
                paciente_id = row["id"]
                conn.execute("UPDATE paciente SET nome = ?, telefone = ?, data_nascimento = ? WHERE id = ?",
                             (nome, tel, nasc, paciente_id))
            else:
                paciente_id = conn.execute(
                    "INSERT INTO paciente (nome, telefone, data_nascimento, telegram_chat_id) VALUES (?, ?, ?, ?)",
                    (nome, tel, nasc, chat_id),
                ).lastrowid

            conn.execute("DELETE FROM paciente_convenio WHERE paciente_id = ?", (paciente_id,))
            if convenio and convenio != "Particular":
                cv = conn.execute("SELECT id FROM convenio WHERE nome = ?", (convenio,)).fetchone()
                if cv:
                    conn.execute(
                        "INSERT INTO paciente_convenio (paciente_id, convenio_id, principal) VALUES (?, ?, 1)",
                        (paciente_id, cv["id"]),
                    )

            if modo != "atualizacao":
                # O consentimento é registrado só agora porque consentimento_lgpd
                # exige paciente_id; a data/hora gravada é a do "SIM" do paciente.
                concedido_em = tracker.get_slot("consent_ts") or agora
                for finalidade, aceitou in (
                    (FINALIDADE_AGENDAMENTO, tracker.get_slot("consent_agendamento") is True),
                    (FINALIDADE_LEMBRETES, tracker.get_slot("consent_lembretes") is True),
                ):
                    if aceitou and not conn.execute(
                        "SELECT 1 FROM consentimento_lgpd WHERE paciente_id = ? AND finalidade = ? AND revogado_em IS NULL",
                        (paciente_id, finalidade),
                    ).fetchone():
                        conn.execute(
                            "INSERT INTO consentimento_lgpd (paciente_id, finalidade, base_legal, concedido_em) "
                            "VALUES (?, ?, ?, ?)",
                            (paciente_id, finalidade, BASE_LEGAL_CONSENTIMENTO, concedido_em),
                        )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            dispatcher.utter_message(
                text="Tive um problema ao salvar seu cadastro. Tente novamente em instantes ou peça para falar com um atendente."
            )
            return limpar + [SlotSet("acao_pendente", None)]
        finally:
            conn.close()

        events = limpar + [SlotSet("paciente_id", paciente_id)]
        if modo == "atualizacao":
            dispatcher.utter_message(text="Pronto, seus dados foram atualizados.")
            return events

        dispatcher.utter_message(text="Cadastro e consentimento registrados. Vamos continuar com o seu agendamento.")
        if tracker.get_slot("acao_pendente"):
            events.append(FollowupAction("action_consultar_disponibilidade"))
        return events


# ---------------------------------------------------------------
# RF11 — Revogação do consentimento (a qualquer momento, sem burocracia)
# ---------------------------------------------------------------
class ActionRevogarConsentimento(Action):
    def name(self) -> Text:
        return "action_revogar_consentimento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        pac = buscar_paciente_por_chat(tracker.sender_id)
        if not pac:
            dispatcher.utter_message(text="Não encontrei nenhum consentimento ativo em seu nome.")
            return []

        texto = _remover_acentos((tracker.latest_message.get("text") or "").lower())
        so_lembretes = "lembrete" in texto
        agora = _agora_iso()

        conn = get_connection()
        try:
            if so_lembretes:
                cur = conn.execute(
                    "UPDATE consentimento_lgpd SET revogado_em = ? "
                    "WHERE paciente_id = ? AND finalidade = ? AND revogado_em IS NULL",
                    (agora, pac["id"], FINALIDADE_LEMBRETES),
                )
            else:
                cur = conn.execute(
                    "UPDATE consentimento_lgpd SET revogado_em = ? WHERE paciente_id = ? AND revogado_em IS NULL",
                    (agora, pac["id"]),
                )
            revogados = cur.rowcount
            conn.execute(
                "UPDATE lembrete SET status_confirmacao = 'ignorado' "
                "WHERE status_confirmacao = 'pendente' "
                "AND consulta_id IN (SELECT id FROM consulta WHERE paciente_id = ?)",
                (pac["id"],),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            dispatcher.utter_message(text="Não consegui registrar a revogação agora. Tente novamente ou peça para falar com um atendente.")
            return []
        finally:
            conn.close()

        if revogados == 0:
            dispatcher.utter_message(text="Você não tinha esse consentimento ativo, então não há nada a revogar.")
        elif so_lembretes:
            dispatcher.utter_message(text="Pronto, você não receberá mais lembretes. Suas consultas continuam agendadas.")
        else:
            dispatcher.utter_message(text=(
                "Consentimento revogado. Não vou mais tratar seus dados para novos atendimentos nem enviar lembretes. "
                "Consultas já marcadas continuam valendo (para cancelá-las, basta pedir). "
                "Para excluir seus dados, posso encaminhar você à recepção. "
                "Se quiser voltar a usar o assistente, peça para agendar e eu peço sua autorização de novo."
            ))
        return [SlotSet("horario_id_disponivel", None), SlotSet("acao_pendente", None)] if not so_lembretes else []


# ---------------------------------------------------------------
# RF09 — Confirmação de presença a partir do lembrete recebido
# ---------------------------------------------------------------
def confirmar_presenca_pendente(paciente_id: int) -> str:
    """Marca como 'confirmado' o lembrete já enviado e ainda pendente da
    próxima consulta do paciente. Devolve 'ok', 'nenhum' ou 'erro'."""
    agora = datetime.now()
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT l.id AS lid
            FROM lembrete l
            JOIN consulta c ON c.id = l.consulta_id
            JOIN horario h ON h.id = c.horario_id
            WHERE c.paciente_id = ? AND c.status IN ({_ATIVOS_SQL})
              AND l.status_confirmacao = 'pendente' AND l.enviado_em IS NOT NULL
              AND (h.data > ? OR (h.data = ? AND h.hora_inicio > ?))
            ORDER BY l.enviado_em DESC LIMIT 1
            """,
            (paciente_id, agora.date().isoformat(), agora.date().isoformat(), agora.strftime("%H:%M")),
        ).fetchone()
        if not row:
            return "nenhum"
        conn.execute("UPDATE lembrete SET status_confirmacao = 'confirmado' WHERE id = ?", (row["lid"],))
        conn.commit()
        return "ok"
    except sqlite3.Error:
        conn.rollback()
        return "erro"
    finally:
        conn.close()


class ActionConfirmarPresenca(Action):
    def name(self) -> Text:
        return "action_confirmar_presenca"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        paciente_id = resolver_paciente_id(tracker)

        # "confirmo" pode ser classificado como confirmarLembrete mesmo quando
        # o paciente está respondendo a "Confirma o agendamento?" (as duas
        # intenções são quase idênticas fora de contexto). Se há um agendamento,
        # reagendamento ou cancelamento esperando confirmação, é isso que ele
        # está confirmando: segue o mesmo caminho do "sim".
        if cancelamento_pendente_valido(tracker) or (
            tracker.get_slot("acao_pendente") and tracker.get_slot("horario_id_disponivel")
        ):
            events = ActionConfirmarAgendamento().run(dispatcher, tracker, domain)
            consulta_id = next((e.get("value") for e in events
                                if e.get("event") == "slot" and e.get("name") == "consulta_id"), None)
            registrar_lembrete(consulta_id, paciente_id)
            return events

        if not paciente_id:
            dispatcher.utter_message(response="utter_consulta_nao_encontrada")
            return []

        resultado = confirmar_presenca_pendente(paciente_id)
        if resultado == "nenhum":
            dispatcher.utter_message(text="Não há nenhum lembrete aguardando confirmação no momento.")
            return []
        if resultado == "erro":
            dispatcher.utter_message(text="Não consegui registrar sua confirmação agora. Tente novamente em instantes.")
            return []

        dispatcher.utter_message(response="utter_agradecer_confirmacao")
        return []