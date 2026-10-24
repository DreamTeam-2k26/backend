# Brainstorm Inteligente — backend (design)

**Data:** 24/10/2026 · **Branch:** `Feat-52` (a partir de `dev` em `5440ad1`) · **Status:** aprovado na conversa, aguardando revisão desta spec.
**Frontend correspondente:** Frontend #60 (fora deste escopo).

## 1. Objetivo

Fórum privado por projeto: tópicos, respostas encadeadas, votos ±1, encerramento, síntese por IA sob demanda,
decisão humana versionada (dono/admin) e criação/vínculo opcional de tarefas no Kanban existente.
Sucesso = fluxo completo via API, com permissões no servidor, testes verdes, `pdm lint` e
`makemigrations --check` limpos, Swagger documentado.

## 2. Decisões tomadas com o integrante

| Tema | Decisão |
|---|---|
| Provedor de IA | OpenRouter com modelo gratuito (API de chat compatível com a OpenAI), `AI_API_KEY` compartilhada com o resumo de reuniões. *Atualizado em 24/10: Claude → DeepSeek → Gemma gratuito.* Trava `AI_ALLOW_PAID` (desligada) bloqueia modelos pagos. |
| Modelo | `google/gemma-4-31b-it:free` (`AI_MODEL`); trocável por `BRAINSTORM_AI_MODEL`; provedor trocável por `AI_API_URL`. |
| Execução | Síncrona no `POST analyze/` (limite de tempo); sem fila, polling ou WebSocket. |
| Reuso | Cliente HTTP compartilhado em `core/ai.py` (não `core/services/`, proibido pelo `CLAUDE.md`). `calls/summarizer.py` passa a usá-lo mantendo `api_key`/`request_summary`/mensagens (os testes de calls fazem mock desses nomes). |
| Arquivado | Definitivo: somente leitura, sem desarquivar. |
| Paginação | A do projeto (`page`, `page_size`, `total_pages`, `count`, `results`); não há `next/previous`. |
| Arquitetura | Tudo em `core/`, padrão do tutorial: model → serializer por ação → ViewSet/@action → router. Regras de objeto em `core/permissions.py`; gravações em vários passos no `create` do serializer com `@transaction.atomic`. |

## 3. Modelos (`core/models/`, um arquivo por entidade)

### BrainstormTopic (`brainstorm_topic.py`)
- `project` FK Project CASCADE · `author` FK User SET_NULL.
- `title` (máx. 150) · `category`: `ideas` (Ideias), `questions` (Dúvidas), `planning` (Planejamento), `decisions` (Decisões).
- `context` (texto, obrigatório) · `initial_suggestion` (texto, opcional).
- `status`: `open` (Em discussão), `closed` (Encerrada), `decided` (Decisão aprovada), `archived` (Arquivada).
- `cycle` (inteiro ≥ 1, começa em 1; +1 a cada `reopen`).
- `current_decision` FK BrainstormDecision SET_NULL, null (decisão vigente).
- `created_at`, `updated_at`, `closed_at`, `archived_at`.
- Índices: (project, status), (project, category), (project, created_at).
- **Em revisão** (`in_review`, calculado): `current_decision` existe e `current_decision.cycle < cycle`.

### BrainstormReply (`brainstorm_reply.py`)
- `topic` CASCADE · `author` SET_NULL · `parent` FK self CASCADE, null · `content` (máx. 5000) · datas.
- `parent` precisa ser do mesmo tópico (validado no serializer). Ordenação `('created_at', 'id')`.

### BrainstormVote (`brainstorm_vote.py`)
- `user` CASCADE · `topic` FK null · `reply` FK null · `value` SmallInteger (+1/-1) · datas.
- Constraints: `CheckConstraint` exatamente um alvo; `UniqueConstraint(user, topic)` com condição `reply IS NULL`;
  `UniqueConstraint(user, reply)`.
- Votar `0` apaga o voto; votar de novo troca o valor (update_or_create), sem duplicar.

### BrainstormAnalysis (`brainstorm_analysis.py`)
- `topic` CASCADE · `cycle` · `status`: `pending`, `running`, `ready`, `failed`.
- `result` JSON (formato validado, §6) · `model` · `error` (mensagem segura, sem dados do provedor) ·
  `requested_by` SET_NULL · `created_at`, `finished_at`.
- Nunca apagadas: cada `analyze` cria uma nova linha. Índice (topic, cycle).

### BrainstormDecision (`brainstorm_decision.py`)
- `topic` CASCADE · `cycle` · `analysis` FK PROTECT · `text` · `justification` (opcional) ·
  `approved_by` SET_NULL · `approved_at` · `rejected_suggestions` JSON (índices de `task_suggestions` não aprovados).
- `UniqueConstraint(topic, cycle)` — base da idempotência. Sem endpoint de edição: histórico imutável.

### BrainstormDecisionTask (no mesmo arquivo da decisão)
- `decision` CASCADE · `task` FK Task SET_NULL, null · `suggestion_index` (null quando vínculo manual) ·
  `title` (cópia no momento da aprovação) · `created` (bool: criada pela decisão × tarefa existente vinculada).

## 4. Status, transições e permissões

```
open ──close──▶ closed ──analyze──▶ (análise ready do ciclo) ──approve──▶ decided
  ▲               │                                                         │
  └───reopen──────┴──────────────────── reopen (cycle + 1) ─────────────────┘
open | closed | decided ──archive──▶ archived (definitivo, somente leitura)
```

| Ação | Quem | Pré-condição |
|---|---|---|
| Ler tópicos, respostas, votos, análise, versões | Participante | — |
| Criar tópico | Participante | — |
| Editar tópico (`PATCH`) | Autor | `open` |
| Excluir tópico | Autor, dono ou admin | Sem respostas, votos, análises e decisões; senão 400 indicando arquivar |
| Criar resposta | Participante | Tópico `open`; `parent` do mesmo tópico |
| Editar resposta | Autor | Tópico `open` |
| Excluir resposta | Autor, dono ou admin | Tópico `open`; sem respostas filhas (400) |
| Votar no tópico/resposta | Participante | Tópico `open` |
| `close` | Dono, admin | `open` |
| `reopen` | Dono, admin | `closed` ou `decided`; `cycle += 1` |
| `archive` | Dono, admin | Diferente de `archived` |
| `analyze` | Dono, admin | `closed` |
| `approve` | Dono, admin | `closed` + análise `ready` do ciclo atual |

- JWT obrigatório (401). Fora do projeto ⇒ 404 (o `get_queryset` filtra por `Membership`). Papel insuficiente ⇒ 403.
  Status que bloqueia a ação ⇒ 400 com mensagem em português.
- Usuário removido do projeto perde acesso imediatamente (consulta de `Membership` a cada requisição).
- Reabrir não altera decisões nem tarefas: a decisão anterior continua em `current_decision` (`in_review = true`)
  até uma nova aprovação.

## 5. API

| Operação | Endpoint |
|---|---|
| Listar/pesquisar | `GET /api/brainstorm-topics/?project=&search=&category=&status=&ordering=` |
| Criar | `POST /api/brainstorm-topics/` |
| Detalhe/editar/excluir | `GET/PATCH/DELETE /api/brainstorm-topics/{id}/` |
| Respostas | `GET/POST /api/brainstorm-replies/?topic=` · `PATCH/DELETE /api/brainstorm-replies/{id}/` |
| Votar | `POST /api/brainstorm-topics/{id}/vote/` · `POST /api/brainstorm-replies/{id}/vote/` — `{"value": -1 \| 0 \| 1}` |
| Transições | `POST .../{id}/close/`, `reopen/`, `archive/` |
| IA | `POST .../{id}/analyze/` · `GET .../{id}/analysis/` |
| Versões | `GET .../{id}/versions/` |
| Aprovar | `POST .../{id}/approve/` |

- **Lista:** `id, project, title, category, status, author, author_name, replies_count, score, upvotes,
  downvotes, my_vote, cycle, in_review, created_at, updated_at, closed_at`. Busca em `title` e `context`;
  ordenação por `created_at`, `score`, `replies_count` (padrão `-created_at`).
- **Detalhe:** lista + `context`, `initial_suggestion`, `archived_at`, `current_decision` (resumo).
- **Respostas:** `id, topic, parent, author, author_name, content, score, upvotes, downvotes, my_vote, created_at, updated_at`.
- **Voto:** resposta `{"score", "upvotes", "downvotes", "my_vote"}`.
- **`analysis/`:** análise mais recente do ciclo atual (404 se não houver).
- **`versions/`:** por ciclo, as análises (id, status, datas) e a decisão aprovada com as tarefas.
- Exemplos de payload no Swagger (`OpenApiExample`).

## 6. IA

### `core/ai.py` (compartilhado)
- `api_key()`, `is_available()`, `complete(*, system, prompt, model, max_tokens, timeout) -> str`.
- Exceções: `AINotConfigured` (sem chave), `AIUnavailable` (rede, timeout, HTTP de erro, resposta sem texto).
- HTTPS puro com `urllib` (sem dependência nova), URL `AI_API_URL` (padrão `https://openrouter.ai/api/v1/chat/completions`), corpo `{model, max_tokens, messages}`, `Authorization: Bearer`.
- `calls/summarizer.py` delega o HTTP para `core.ai.complete`, mantendo `SummaryUnavailableError`, `api_key`,
  `request_summary`, `SYSTEM_PROMPT` e mensagens atuais.

### Análise do tópico
- Configuração: `AI_API_KEY`, `BRAINSTORM_AI_MODEL` (padrão `google/gemma-4-31b-it:free`), `AI_ALLOW_PAID`, timeout 60 s,
  contexto até 40 000 caracteres, saída até 3000 tokens.
- Contexto enviado: título, categoria, contexto, sugestão inicial, votos do tópico; respostas como
  `[R<id>] (resposta a R<parent>) score=<n>: <texto>` (as mais votadas primeiro se precisar cortar);
  tarefas do projeto (`[T<id>] título — coluna`, até 200) para detectar duplicatas.
- Conteúdo dos usuários vai entre tags e o system prompt diz que é **dado não confiável**, nunca instrução.
- A IA deve responder somente JSON:
  ```json
  {
    "main_solution": "texto",
    "reasoning": "texto",
    "alternatives": [{"title": "", "description": "", "sources": [12]}],
    "sources": [12, 15],
    "ai_additions": ["sugestão nova da IA"],
    "task_suggestions": [
      {"title": "", "description": "", "priority": "medium", "existing_task_id": null, "sources": [15]}
    ]
  }
  ```
- Validação antes de salvar: JSON parseável (aceita bloco ```json), `main_solution` não vazio, tipos corretos,
  textos e listas com limite (alternativas ≤ 5, sugestões ≤ 10, títulos ≤ 200), `sources` só com ids de
  respostas reais do tópico (inválidos descartados), `existing_task_id` só de tarefa do mesmo projeto
  (senão `null`), prioridade fora de `low|medium|high` vira `medium`.
- Respostas da API: 201 com a análise `ready`. Sem chave ⇒ 503 `ai_not_configured` (nada gravado).
  Falha de rede/provedor ⇒ análise `failed` + 503 `ai_unavailable`. JSON inválido ⇒ análise `failed` +
  502 `ai_invalid_response`. A discussão nunca é alterada pela análise.
- A IA não roda ao criar, editar ou votar.

## 7. Aprovação e Kanban

Entrada:
```json
{
  "analysis": 7,
  "text": "Decisão final (editável)",
  "justification": "Opcional",
  "tasks": [
    {"suggestion_index": 0, "title": "Montar protótipo", "description": "", "priority": "high",
     "assignee": 3, "due_date": "2026-11-01"},
    {"existing_task_id": 42}
  ]
}
```
- Exige tópico `closed`, `analysis` do tópico, do ciclo atual e `ready`; `text` obrigatório.
- `@transaction.atomic` com `select_for_update` no tópico.
- Já existe decisão para `(topic, cycle)`: mesma `analysis` ⇒ 200 com a decisão existente (sem criar nada);
  outra `analysis` ⇒ 409.
- Tarefa nova: coluna `todo` de menor posição do projeto (400 se não existir), validada pelo
  `TaskCreateUpdateSerializer` real (responsável participante, prioridade, prazo ≥ hoje); `created_by` = aprovador.
- Tarefa existente: precisa ser do mesmo projeto (400); não é alterada. Itens repetidos são recusados (400).
- `tasks` vazio ⇒ nenhuma tarefa criada.
- `rejected_suggestions` = índices de `task_suggestions` não referenciados.
- Ao final: tópico `decided`, `current_decision` = nova decisão. Resposta 201 com decisão + tarefas.
- Aprovações de outros ciclos nunca alteram tarefas anteriores.

## 8. Testes (`core/tests/test_brainstorm_*.py`)

IA sempre simulada (`mock.patch` em `core.ai.complete`). Casos:
- Fluxo completo: criar → responder (thread) → votar → encerrar → analisar → aprovar com tarefa nova e existente.
- Isolamento: outro projeto ⇒ 404 em tudo; usuário removido perde acesso; 401 sem token; membro ⇒ 403 nas ações de dono/admin.
- Votos: repetir, trocar, remover com 0, voto em resposta, contadores e `my_vote`.
- Threads: `parent` de outro tópico ⇒ 400; excluir resposta com filhas ⇒ 400.
- Status: escrita em `closed`/`archived` ⇒ 400; excluir tópico com interação ⇒ 400; `analyze` em `open` ⇒ 400.
- IA: sem chave ⇒ 503; erro ⇒ `failed` + 503; JSON inválido ⇒ 502; sources inventados descartados; prompt marca dados como não confiáveis.
- Versões: reabrir mantém decisão vigente com `in_review`; aprovar com análise de ciclo antigo ⇒ 400; nova aprovação troca a vigente.
- Idempotência: POST de aprovação repetido não duplica tarefas; outra análise no mesmo ciclo ⇒ 409.
- Regressão: suíte de `calls` continua verde após o uso de `core.ai`.

Verificação final: `pdm test`, `pdm lint`, `pdm run python manage.py makemigrations --check --dry-run`.

## 9. Documentação

- README: seção "Brainstorm" com endpoints, regras, limites, variáveis (`AI_API_KEY`, `AI_API_URL`, `AI_MODEL`, `BRAINSTORM_AI_MODEL`, `AI_ALLOW_PAID`)
  e como testar sem chave (503) e com chave.
- Swagger: `extend_schema` com resumo, request e exemplos em todas as ações.

## 10. Fora do escopo

Frontend, histórico global (#48), notificações, WebSocket/polling, anexos, criação de tarefas sem aprovação,
alterações no Kanban, Chat, chamadas (exceto a troca do HTTP para `core.ai`) e no sistema de permissões existente.
