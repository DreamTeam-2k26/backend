# Template Django + PDM — Hackathon 2026

API REST com Django, Django REST Framework, JWT, Channels (WebSocket) e upload de arquivos, gerenciada com [PDM](https://pdm-project.org).

## Requisitos

- Python 3.14
- PDM (`pip install --user pdm`)
- **Linux/macOS:** `libmagic` instalado no sistema (usado para detectar o tipo real dos uploads)
  - Debian/Ubuntu: `sudo apt install libmagic1`
  - macOS: `brew install libmagic`
  - Windows: não precisa, o `python-magic-bin` já traz a biblioteca.

## Primeiros passos

```bash
pdm install
cp .env.example .env
pdm migrate
pdm createsuperuser
pdm dev
```

A API sobe em http://127.0.0.1:8000/api/ e a documentação em http://127.0.0.1:8000/api/doc/ (Swagger) ou `/api/redoc/`.

## Scripts PDM

| Comando | O que faz |
| --- | --- |
| `pdm dev` | Servidor de desenvolvimento (ASGI via daphne, com recarga automática) |
| `pdm serve` | daphne sem recarga, para produção/containers |
| `pdm makemigrations` / `pdm migrate` | Gera e aplica migrations |
| `pdm createsuperuser` | Cria um superusuário |
| `pdm shell` / `pdm shellp` | Shell do Django / shell_plus |
| `pdm test` | Roda os testes |
| `pdm lint` | `ruff check` + `ruff format --check` |
| `pdm format` | Corrige e formata o código com o Ruff |
| `pdm loaddata <arquivo>` / `pdm dumpdata <app>` | Importa/exporta fixtures |

## Variáveis de ambiente

Todas estão documentadas em [`.env.example`](.env.example). Os principais comportamentos:

- `DEBUG=False` exige `SECRET_KEY` e ativa o whitenoise com manifest (rode `python manage.py collectstatic`).
- `CHAT_MAX_UPLOAD_MB` (padrão 10) limita o tamanho dos anexos do chat.
- Sem `DATABASE_URL` usa SQLite; sem `REDIS_URL` usa a camada do Channels em memória; sem `CLOUDINARY_URL` grava uploads em `media/`.

## Estrutura

- `app/` — configuração do projeto (settings, urls, asgi, paginação)
- `core/` — usuário customizado (login por e-mail), grupos, permissões, JWT e middleware de WebSocket
- `uploader/` — upload de imagens (JPEG/PNG) e documentos (PDF)
- `chat/` — chat em tempo real em salas coletivas (REST + WebSocket); veja a seção [Chat](#chat)

## Endpoints

| Método | Rota | Acesso |
| --- | --- | --- |
| POST | `/api/register/` | Público — cria conta (`email`, `password`) |
| POST | `/api/token/`, `/api/token/refresh/`, `/api/token/verify/` | Público — JWT |
| GET | `/api/users/me/` | Autenticado |
| GET/PATCH/DELETE | `/api/users/{id}/` | O próprio usuário ou equipe (só superusuário altera superusuário) |
| GET/POST | `/api/users/` | Lista: equipe vê todos, demais só a si; criação: equipe |
| GET | `/api/groups/`, `/api/permissions/` | Autenticado (escrita em grupos: superusuário) |
| POST | `/api/media/images/`, `/api/media/documents/` | Autenticado (multipart, campo `file`) |

### Foto de perfil

1. Faça login e envie a imagem para `POST /api/media/images/`; a resposta traz o `attachment_key`.
2. `PATCH /api/users/{id}/` com `profile_picture_attachment_key`. Só é aceita uma imagem enviada pelo próprio usuário.

O `attachment_key` funciona como comprovante de posse: não existe endpoint que liste uploads.

## WebSocket

Rotas em `core/routing.py`. A autenticação usa o JWT na query string: `ws://host/ws/...?token=<access>`. Evite logar URLs de WebSocket, já que o token vai nelas.

## Chat

App `chat/`: salas coletivas (sem conversas privadas 1:1 e sem sala pública global). Só participantes (`Membership`) leem ou escrevem; quem não participa recebe 404, igual a uma sala inexistente. Usa o mesmo usuário e JWT do restante da plataforma; o autor vem sempre do token, nunca do cliente. Não adiciona dependências.

### REST (`/api/chat/`, JWT `Authorization: Bearer`)

| Método | Rota | Descrição |
| --- | --- | --- |
| GET/POST | `rooms/` | Lista as salas do usuário (com não lidas e última mensagem; paginada, use `page_size=100`) / cria sala (`name`, `members`, `image_attachment_key`) |
| GET/PATCH | `rooms/{id}/` | Detalhe com membros e presença / renomeia (admin) |
| POST | `rooms/{id}/members/` | Adiciona membros (admin) |
| DELETE | `rooms/{id}/members/{user_id}/` | Remove membro (admin) |
| PUT/DELETE | `rooms/{id}/members/{user_id}/admin/` | Promove / rebaixa admin |
| POST | `rooms/{id}/leave/` | Sai da sala (se ficar vazia, é apagada com seus arquivos) |
| POST | `rooms/{id}/read/` | Marca como lida |
| GET/POST | `rooms/{id}/messages/` | Histórico (`?before=<id>` para páginas antigas) / envia texto ou arquivo (multipart, campo `file`) |
| GET | `messages/{id}/file/` | Baixa anexo (só membros; imagens inline, o resto como download) |
| GET | `users/?search=&exclude_room=` | Busca usuários para convidar (mínimo 2 caracteres; não expõe e-mail) |

### WebSocket `ws://host/ws/chat/?token=<access>`

Uma conexão por aba. Sem token válido a conexão é aceita e fechada com o código **4401** (o cliente não deve reconectar). Quadros acima de 16 KB são recusados.

- Cliente → servidor: `{"action": "send" | "edit" | "delete" | "typing" | "read", "room": <id>, ...}`
- Servidor → cliente: `message.new`, `message.update`, `typing`, `presence`, `read.receipt`, `room.updated`, `room.removed`, `error`

Se o servidor for encerrado de forma abrupta, rode `python manage.py reset_chat_presence` para zerar os contadores de presença.

Limitações conhecidas: sem rate limiting; o token expirado não derruba um socket já aberto; anexos aceitam qualquer tipo até `CHAT_MAX_UPLOAD_MB`; ainda não há entidade de projeto, então as salas são independentes.

## Perfil do usuário

`POST /api/register/` exige `email`, `password` e `name` (1 a 150 caracteres, sem aceitar apenas espaços).
A migração atribui `Usuário` aos cadastros anteriores; novos cadastros precisam informar o nome.

Com o JWT existente, `GET /api/users/me/` retorna `id`, `email`, `name` e `profile_picture` (objeto ou `null`).
`PATCH /api/users/me/` atualiza parcialmente o perfil; `PUT` exige `email` e `name`.
Também continua disponível a atualização em `/api/users/{id}/` do próprio usuário.
Os campos administrativos ficam restritos às operações de gestão autorizadas.

A foto é opcional: após o cadastro, envie uma imagem pelo uploader autenticado e informe
`profile_picture_attachment_key` no perfil. A imagem precisa existir e pertencer ao usuário.
Envie `null` nesse campo para remover a associação sem apagar o upload. O cadastro sem foto
não exige upload; login por e-mail, refresh e verify mantêm as rotas e os contratos existentes.
