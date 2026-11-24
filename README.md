# Chatbot de agendamento de consultas (TCC)

Protótipo de chatbot para agendamento, cancelamento e reagendamento de consultas em clínicas
de pequeno e médio porte, desenvolvido como Trabalho de Conclusão de Curso de Sistemas de
Informação no Centro Universitário Dinâmica das Cataratas (UDC), Foz do Iguaçu.

- **Autor:** Adnan Omar Jibara
- **Orientador:** Prof. Me. Luciano Santos Cardoso

O núcleo roda localmente: **Rasa Open Source 3.6.20** com o modelo **BERTimbau** no pipeline de
compreensão de linguagem, ações em Python, banco **SQLite** e o **Telegram** como canal. O cadastro
do paciente só ocorre após o consentimento por finalidade (LGPD).

O texto completo do trabalho está em [`TCC_Adnan_Jibara.pdf`](TCC_Adnan_Jibara.pdf).

## Estrutura

| Pasta / arquivo | Conteúdo |
|---|---|
| `TCC_Adnan_Jibara.pdf` | Texto completo do TCC, com apêndices |
| `rasa/` | Projeto Rasa: `config.yml`, `domain.yml`, `data/` (corpus, regras), `actions/actions.py` (regras de negócio e acesso ao banco), `telegram_custom.py` (canal do Telegram) e `enviar_lembretes.py` (serviço de lembretes) |
| `schema.sql` | Esquema do banco de dados (DDL) |
| `seed_horarios.py` | Cria o banco a partir do `schema.sql` e gera horários livres |
| `cenarios/` | Testes funcionais automatizados (`cenarios.py`) e os resultados de cada ciclo (`*.csv`) |
| `ablacao/` | Experimento com e sem o BERTimbau e as estatísticas (`teste_corrigido.py`) |
| `resultados/` | Medições de tempo de resposta e o cálculo da pontuação SUS |
| `tcc/` | Texto do TCC em LaTeX e as figuras (`figuras/`, em TikZ) |
| `pacote/pkg/scripts/` | Scripts auxiliares (gráfico de mercado, tabela do corpus, medição de latência) |

## Como executar

Requisitos: Python 3.8 e um bot do Telegram (criado pelo @BotFather).

```
python -m venv .venv
.venv\Scripts\activate
pip install -r rasa\requirements.txt
python seed_horarios.py --reset
cd rasa
copy credentials.exemplo.yml credentials.yml
rasa train
```

No `credentials.yml`, troque `COLOQUE_O_TOKEN_AQUI` pelo token do seu bot. Depois, em três
terminais dentro de `rasa\` (com o ambiente ativo):

```
rasa run
rasa run actions
python enviar_lembretes.py
```

O Telegram precisa alcançar o servidor local por HTTPS (por exemplo, com um túnel como o ngrok,
apontando o webhook do bot para `/webhooks/telegram_custom/webhook`). O serviço de lembretes lê o
token da variável de ambiente `TELEGRAM_BOT_TOKEN`.

## Testes

Com os servidores no ar apontando para um banco de teste (variável `CLINICA_DB`):

```
python cenarios\cenarios.py --execucoes 3
```

## Observações

- Nenhum dado real de paciente é usado; a agenda é simulada.
- O token do bot e os bancos de dados não fazem parte do repositório.
