# Brainstorm Inteligente — backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fórum de brainstorm por projeto com votos, ciclo de encerramento/reabertura, síntese por IA (modelo gratuito via OpenRouter) e aprovação versionada que cria/vincula tarefas.

**Architecture:** Tudo em `core/` no padrão do tutorial (model → serializers por ação → ViewSet/@action → router). Cliente HTTP da IA compartilhado em `core/ai.py`, usado também por `calls/summarizer.py`. Regras de objeto em `core/permissions.py`; gravações multi-tabela em `create` de serializers com `@transaction.atomic`.

**Tech Stack:** Django 6, DRF, django-filter, drf-spectacular, urllib (sem dependência nova), APITestCase + unittest.mock.

**Spec:** `docs/superpowers/specs/2026-10-24-brainstorm-backend-design.md`

## Global Constraints

- Sem apps novos, sem `core/services/`; `__init__.py` no estilo `from .x import A, B` (não rodar ruff --fix neles).
- Título de tópico máx. 150; resposta máx. 5000; alternativas ≤ 5; sugestões ≤ 10; títulos de sugestão ≤ 200.
- IA: `AI_API_KEY`, `AI_API_URL`, `BRAINSTORM_AI_MODEL` (padrão `google/gemma-4-31b-it:free`; `AI_ALLOW_PAID` desligado bloqueia modelos pagos), timeout 60 s, contexto ≤ 40 000 caracteres, saída ≤ 3000 tokens, tarefas no contexto ≤ 200.
- Erros da IA: 503 `ai_not_configured`, 503 `ai_unavailable`, 502 `ai_invalid_response`.
- Fora do projeto ⇒ 404; papel insuficiente ⇒ 403; status bloqueante ⇒ 400; sem token ⇒ 401. Mensagens em português.
- Paginação padrão do projeto (`app.pagination.CustomPagination`).
- Não fazer commit/push sem pedido explícito (CLAUDE.md); os passos de commit ficam para quando o integrante autorizar.

## Review Focus

- Usuário removido do projeto no meio do debate: deve receber 404 em leitura e escrita (teste em Task 2).
- Voto em resposta de tópico encerrado: precisa ser bloqueado como o voto no tópico (teste em Task 3).
- IA devolvendo JSON dentro de bloco ```json ou com campos extras/errados: aceitar o bloco, descartar o resto, nunca 500 (teste em Task 5).
- Aprovação com `assignee` de fora do projeto ou prazo no passado: 400 e nenhuma tarefa criada (atomicidade, teste em Task 6).
- Projeto sem coluna `todo` (todas renomeadas/recategorizadas) ao aprovar tarefa nova: 400 claro (teste em Task 6).

---

### Task 1: Models, migration e admin

**Files:**
- Create: `core/models/brainstorm_topic.py`, `brainstorm_reply.py`, `brainstorm_vote.py`, `brainstorm_analysis.py`, `brainstorm_decision.py`
- Modify: `core/models/__init__.py`, `core/admin.py`
- Create: `core/migrations/00XX_brainstorm.py` (via makemigrations)
- Test: `core/tests/test_brainstorm_models.py`

**Interfaces — Produces:**
- `BrainstormTopic.Category` (`ideas|questions|planning|decisions`), `BrainstormTopic.Status` (`open|closed|decided|archived`), campos da spec §3, propriedade `in_review: bool`, `has_interactions() -> bool`.
- `BrainstormReply(topic, author, parent, content)`; `BrainstormVote(user, topic|reply, value)`; `BrainstormAnalysis.Status`; `BrainstormDecision(topic, cycle, analysis, text, justification, approved_by, approved_at, rejected_suggestions)`; `BrainstormDecisionTask(decision, task, suggestion_index, title, created)`.
- Constantes: `TOPIC_TITLE_MAX = 150`, `REPLY_CONTENT_MAX = 5000`.

- [ ] Testes: `__str__`; `in_review` falso sem decisão, verdadeiro com decisão de ciclo menor; constraint de voto com dois alvos ou nenhum ⇒ `IntegrityError`; voto duplicado (user, topic) ⇒ `IntegrityError`; decisão duplicada (topic, cycle) ⇒ `IntegrityError`; `has_interactions` com reply/voto/análise.
- [ ] Rodar: `pdm run python manage.py test core.tests.test_brainstorm_models` ⇒ falha (import).
- [ ] Implementar models, exportar, admin, `pdm makemigrations core`.
- [ ] Rodar de novo ⇒ PASS.

### Task 2: Tópicos — CRUD, filtros, permissões, transições

**Files:**
- Create: `core/serializers/brainstorm_topic.py`, `core/views/brainstorm_topic.py`
- Modify: `core/serializers/__init__.py`, `core/views/__init__.py`, `core/permissions.py`, `app/urls.py`
- Test: `core/tests/test_brainstorm_topic_api.py`

**Interfaces — Produces:**
- `BrainstormTopicListSerializer`, `BrainstormTopicRetrieveSerializer`, `BrainstormTopicCreateUpdateSerializer`.
- `BrainstormTopicPermission` (usa `obj.my_role`; ações de gestão = owner/admin; PATCH = autor; DELETE = autor ou owner/admin).
- `BrainstormTopicViewSet` em `/api/brainstorm-topics/`; `get_queryset` anota `my_role`, `replies_count`, `score`, `upvotes`, `downvotes`, `my_vote`.
- Ações `close`, `reopen`, `archive` (POST, `request=None`, resposta = Retrieve).
- Helper `ensure_open(topic)` (levanta `ValidationError` 400 se não `open`) em `core/serializers/brainstorm_topic.py`, reutilizado pelas Tasks 3–4.

- [ ] Testes: criar (autor do token, status `open`, ciclo 1); 401; outro projeto ⇒ 404 em list/retrieve/create; usuário removido ⇒ 404; busca/filtro `category`/`status`/ordering `-score`; PATCH autor ok, outro membro 403, em `closed` 400; DELETE sem interação 204, com reply 400; membro em `close` 403; close→reopen soma ciclo; `archive` torna somente leitura (PATCH/close 400); reopen de `archived` 400.
- [ ] Rodar ⇒ falha. Implementar. Rodar ⇒ PASS.

### Task 3: Respostas e votos

**Files:**
- Create: `core/serializers/brainstorm_reply.py`, `core/serializers/brainstorm_vote.py`, `core/views/brainstorm_reply.py`
- Modify: `core/views/brainstorm_topic.py` (ação `vote`), `__init__.py`s, `core/permissions.py`, `app/urls.py`
- Test: `core/tests/test_brainstorm_reply_api.py`, `core/tests/test_brainstorm_vote_api.py`

**Interfaces — Produces:**
- `BrainstormReplyListSerializer`, `BrainstormReplyCreateUpdateSerializer` (`topic`, `parent`, `content`; `topic` fixo no update).
- `BrainstormVoteSerializer` (`value` em `{-1, 0, 1}`) com `save(user=, topic=|reply=) -> dict` que devolve `{score, upvotes, downvotes, my_vote}`.
- `BrainstormReplyPermission`; `BrainstormReplyViewSet` em `/api/brainstorm-replies/` com filtro `topic` e ação `vote`.

- [ ] Testes: thread (parent do mesmo tópico ok, de outro tópico 400); resposta em tópico `closed` 400; editar só autor; excluir com filhas 400; dono exclui resposta de membro; votar +1, repetir (sem duplicar), trocar para -1, 0 remove; `my_vote` por usuário; voto em resposta de tópico encerrado 400; valor 2 ⇒ 400; fora do projeto 404.
- [ ] Rodar ⇒ falha. Implementar. Rodar ⇒ PASS.

### Task 4: `core/ai.py` + refatoração de `calls/summarizer.py`

**Files:**
- Create: `core/ai.py`
- Modify: `calls/summarizer.py`, `app/settings.py` (`BRAINSTORM_AI_MODEL`)
- Test: `core/tests/test_ai.py`; regressão `calls/tests/`

**Interfaces — Produces:**
- `api_key() -> str`, `is_available() -> bool`, `complete(*, system: str, prompt: str, model: str, max_tokens: int, timeout: int) -> str`.
- `class AIError(Exception)`, `class AINotConfigured(AIError)`, `class AIUnavailable(AIError)`.
- `summarizer.request_summary(prompt)` passa a chamar `core.ai.complete` e converte `AIError` em `SummaryUnavailableError` com as mensagens atuais.

- [ ] Testes (mock de `urllib.request.urlopen`): sem chave ⇒ `AINotConfigured` sem rede; corpo enviado tem `model`, `system`, `max_tokens` e header `x-api-key`; `URLError`/timeout ⇒ `AIUnavailable`; resposta sem texto ⇒ `AIUnavailable`; texto concatenado dos blocos `text`.
- [ ] Rodar ⇒ falha. Implementar. Rodar `core.tests.test_ai` e `calls` ⇒ PASS.

### Task 5: Análise por IA

**Files:**
- Create: `core/serializers/brainstorm_analysis.py`
- Modify: `core/views/brainstorm_topic.py` (ações `analyze`, `analysis`)
- Test: `core/tests/test_brainstorm_analysis_api.py`

**Interfaces — Produces:**
- `build_analysis_prompt(topic) -> str`, `parse_analysis(text: str, topic) -> dict` (levanta `ValueError` se inválido), `ANALYSIS_SYSTEM_PROMPT` em `core/serializers/brainstorm_analysis.py`.
- `BrainstormAnalysisSerializer` (leitura: `id, topic, cycle, status, result, model, error, requested_by, created_at, finished_at`).
- `analyze` ⇒ 201 com análise; erros conforme Global Constraints com corpo `{"detail", "code"}`.

- [ ] Testes (mock `core.ai.complete`): `open` ⇒ 400; membro ⇒ 403; sem chave ⇒ 503 `ai_not_configured` sem gravar; erro ⇒ `failed` + 503; JSON inválido ⇒ `failed` + 502; bloco ```json aceito; sources inventados descartados; `existing_task_id` de outro projeto vira `null`; prioridade inválida vira `medium`; prompt contém `[R<id>]`, `[T<id>]` e o aviso de dado não confiável; `analysis/` devolve a mais recente do ciclo; sem análise 404; discussão inalterada após falha.
- [ ] Rodar ⇒ falha. Implementar. Rodar ⇒ PASS.

### Task 6: Aprovação, versões e Kanban

**Files:**
- Create: `core/serializers/brainstorm_decision.py`
- Modify: `core/views/brainstorm_topic.py` (ações `approve`, `versions`)
- Test: `core/tests/test_brainstorm_decision_api.py`

**Interfaces — Consumes:** `TaskCreateUpdateSerializer` (`core/serializers/task.py`), `Column.Category.TODO`.
**Produces:** `BrainstormApproveSerializer` (entrada da spec §7, `create()` atômico) e `BrainstormDecisionSerializer` (saída com `tasks`).

- [ ] Testes: fluxo completo cria tarefa em `todo` com `created_by` = aprovador e vincula existente sem alterá-la; `tasks` vazio não cria tarefa; POST repetido ⇒ 200 e mesma contagem de tarefas; outra análise no mesmo ciclo ⇒ 409; análise de ciclo antigo ⇒ 400; `assignee` de fora ⇒ 400 e nenhuma tarefa/decisão criada; tarefa existente de outro projeto ⇒ 400; projeto sem coluna `todo` ⇒ 400; membro ⇒ 403; reabrir após decidir ⇒ `in_review` true e `current_decision` mantida; nova aprovação troca a vigente e não mexe nas tarefas antigas; `versions/` lista ciclos.
- [ ] Rodar ⇒ falha. Implementar. Rodar ⇒ PASS.

### Task 7: Documentação e verificação final

**Files:** Modify `README.md` (seção Brainstorm), exemplos `OpenApiExample` nas ações.

- [ ] Rodar `pdm test` (suíte inteira), `pdm lint`, `pdm run python manage.py makemigrations --check --dry-run`, `core.tests.test_schema` ⇒ tudo verde.
