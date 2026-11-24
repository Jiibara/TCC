import asyncio
import logging
from collections import OrderedDict

import requests
from sanic import Blueprint, response
from sanic.request import Request
from rasa.core.channels.channel import InputChannel, UserMessage, OutputChannel

logger = logging.getLogger(__name__)


class TelegramOutput(OutputChannel):
    @classmethod
    def name(cls):
        return "telegram_custom"

    def __init__(self, token):
        self.api = f"https://api.telegram.org/bot{token}"

    async def send_text_message(self, recipient_id, text, **kwargs):
        requests.post(
            f"{self.api}/sendMessage",
            json={"chat_id": recipient_id, "text": text},
            timeout=10,
        )


class TelegramCustomInput(InputChannel):
    @classmethod
    def name(cls):
        return "telegram_custom"

    @classmethod
    def from_credentials(cls, credentials):
        credentials = credentials or {}
        return cls(credentials.get("access_token"))

    # Quantos update_id recentes guardar para descartar reenvios do Telegram.
    MAX_UPDATES_VISTOS = 1000

    def __init__(self, access_token):
        self.access_token = access_token
        self.updates_vistos = OrderedDict()
        self.tarefas = set()  # mantém referência às tarefas em segundo plano

    def ja_processado(self, update_id) -> bool:
        """O Telegram reenvia a mesma atualização (mesmo update_id) quando o
        webhook demora a responder. Devolve True se ela já foi recebida."""
        if update_id is None:
            return False
        if update_id in self.updates_vistos:
            return True
        self.updates_vistos[update_id] = True
        if len(self.updates_vistos) > self.MAX_UPDATES_VISTOS:
            self.updates_vistos.popitem(last=False)
        return False

    def blueprint(self, on_new_message):
        tg = Blueprint("telegram_custom_webhook", __name__)

        @tg.route("/", methods=["GET"])
        async def health(request: Request):
            return response.json({"status": "ok"})

        @tg.route("/webhook", methods=["POST"])
        async def message(request: Request):
            data = request.json or {}
            msg = data.get("message")
            if not msg or "text" not in msg:
                return response.text("ok")
            if self.ja_processado(data.get("update_id")):
                logger.info("update_id %s repetido: descartado", data.get("update_id"))
                return response.text("ok")
            chat_id = str(msg["chat"]["id"])
            text = msg["text"]
            out = TelegramOutput(self.access_token)
            # Confirma o recebimento na hora e processa em segundo plano,
            # para o Telegram não reenviar a mensagem por demora.
            tarefa = asyncio.create_task(
                on_new_message(UserMessage(text, out, chat_id, input_channel=self.name()))
            )
            self.tarefas.add(tarefa)
            tarefa.add_done_callback(self.tarefas.discard)
            return response.text("ok")

        return tg