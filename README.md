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
- `CALL_MAX_PARTICIPANTS` (padrão 8) limita as pessoas por chamada; `CALL_ICE_SERVERS` (JSON) troca os servidores STUN/TURN das chamadas (veja [Chamadas](#chamadas)).
- Sem `DATABASE_URL` usa SQLite; sem `REDIS_URL` usa a camada do Channels em memória; sem `CLOUDINARY_URL` grava uploads em `media/`.

## Estrutura

- `app/` — configuração do projeto (settings, urls, asgi, paginação)
- `core/` — usuário customizado (login por e-mail), grupos, permissões, JWT e middleware de WebSocket
- `uploader/` — upload de imagens (JPEG/PNG) e documentos (PDF)
- `chat/` — chat em tempo real em salas coletivas (REST + WebSocket); veja a seção [Chat](#chat)
- `calls/` — chamadas de voz e vídeo dos projetos (REST + WebSocket de sinalização); veja a seção [Chamadas](#chamadas)

## Endpoints

| Método | Rota | Acesso |
| --- | --- | --- |
| POST | `/api/register/` | Público — cria conta (`email`, `password`) |
| POST | `/api/token/`, `/api/token/refresh/`, `/api/token/verify/` | Público — JWT |
| GET | `/api/users/me/` | Autenticado |
| POST | `/api/auth/google/` | Público — entra/cadastra com Google (`credential`); veja [Login com Google](#login-com-google) |
| GET/POST | `/api/auth/google/link/` | Autenticado — consulta/vincula a conta Google |
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

## Chamadas

App `calls/` (independente de `core.Meeting`, que guarda reuniões com link externo): chamadas de voz/vídeo dentro do projeto. A mídia vai direto entre os navegadores (WebRTC, malha entre todos); o servidor guarda o estado da chamada e repassa a sinalização. Só participantes do projeto veem e entram; quem não participa recebe 404.

- **Quem pode:** dono e administradores iniciam, agendam, editam, encerram e removem; qualquer participante entra enquanto a chamada está em andamento.
- **Estados:** `scheduled` (agendada) → `live` (em andamento) → `ended`. Só uma chamada `live` por projeto; ela encerra sozinha quando a última pessoa sai (ou, vazia, após 10 minutos).
- **REST (`/api/calls/`):** `GET /` (filtros `?project=`, `?status=`), `POST /` (sem `scheduled_for` inicia agora; com ele, agenda), `GET|PATCH|DELETE /{id}/`, `POST /{id}/start/`, `POST /{id}/end/`, `GET /live/`, `GET /config/`.
- **WebSocket `ws://host/ws/calls/?token=<access>`:** avisos `call.changed` das chamadas dos seus projetos.
- **WebSocket `ws://host/ws/calls/<id>/?token=<access>`:** a sala. Servidor → cliente: `welcome`, `peer.joined`, `peer.left`, `peer.state`, `signal`, `call.ended`, `replaced`, `error`. Cliente → servidor: `{"action": "signal", "to": <id>, "data": {...}}`, `{"action": "state", "state": {...}}`, `{"action": "leave"}`. Entrar de outra aba assume o lugar da anterior.
- **Rede:** por padrão usa STUN público do Google. Em redes restritas (firewall corporativo, NAT simétrico) a conexão direta pode falhar: configure um servidor TURN em `CALL_ICE_SERVERS`, por exemplo `[{"urls":"turn:turn.exemplo.com:3478","username":"u","credential":"s"}]`.
- **Limitações:** malha limita o tamanho da chamada (padrão 8 pessoas); não grava nem transcreve; o aviso na hora marcada é do navegador aberto (não há tarefa agendada no servidor).

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

## Login com Google

O frontend usa o **Google Identity Services (GIS)**, que entrega um **ID token** do Google (`response.credential`).
O backend confere esse token com a biblioteca oficial `google-auth` (assinatura pelas chaves públicas do Google,
`aud` igual ao nosso Client ID, `iss`, `exp` e e-mail verificado) e devolve **o mesmo JWT do `POST /api/token/`**.
O ID token do Google nunca é usado como token da API.

### Configuração (uma vez)

1. No [Google Cloud Console](https://console.cloud.google.com/) crie (ou escolha) um projeto.
2. Em **APIs e serviços → Tela de consentimento OAuth**, configure o app (tipo *Externo*, nome "Fluxo",
   e-mail de suporte). Enquanto o app estiver em *teste*, só os e-mails cadastrados como testadores entram.My Project 92734
3. Em **APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth**, escolha
   **Aplicativo da Web** e preencha **Origens JavaScript autorizadas** com as URLs do frontend, por exemplo
   `http://localhost:5173` e `http://localhost` (desenvolvimento) e a URL HTTPS de produção.
   O GIS com callback não usa *URIs de redirecionamento*.
4. Copie o **ID do cliente** (`xxxx.apps.googleusercontent.com`) para o ambiente do backend:
   `GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com`. O frontend usa o mesmo valor.
   **Não existe client secret neste fluxo**: não crie, não guarde e nunca coloque um secret no frontend.

Sem `GOOGLE_CLIENT_ID`, o login tradicional continua normal, `/api/auth/google/` responde 503
(`google_auth_unavailable`) e, com `DEBUG=False`, o `manage.py check` mostra o aviso `core.W001`.

### Endpoints

`POST /api/auth/google/` (público) — corpo `{"credential": "<ID token do GIS>"}`, resposta `{"access": "...", "refresh": "..."}`.

- Conta Google já vinculada (pelo `sub`, nunca pelo e-mail): entra nela.
- Conta nova: cria o usuário com o e-mail verificado, o nome e a foto do Google, **sem senha** (só entra pelo Google),
  no grupo padrão e sem nenhum privilégio extra.
- E-mail já usado por uma conta sem Google: **409 `account_link_required`**. A conta não é tomada; a pessoa
  entra com e-mail e senha e vincula o Google pelo endpoint abaixo.

`POST /api/auth/google/link/` (exige `Authorization: Bearer <access do Fluxo>`) — mesmo corpo; vincula a conta
Google à conta logada e responde `{"linked": true, "email": "..."}`. Repetir com a mesma conta Google é inofensivo.
`GET /api/auth/google/link/` diz se há vínculo: `{"linked": false, "email": null}`.

Desvincular não existe no MVP: quem criou a conta pelo Google não tem senha e ficaria sem como entrar.

### Erros (`{"detail": "...", "code": "..."}`)

| Status | `code` | Quando | O frontend deve |
| --- | --- | --- | --- |
| 400 | — | Falta `credential` | Corrigir a chamada |
| 401 | `invalid_google_token` | Token inválido, expirado ou de outro app | Pedir para entrar com o Google de novo |
| 403 | `google_email_not_verified` | Conta Google sem e-mail verificado | Mostrar a mensagem |
| 403 | `inactive_account` | Conta Fluxo desativada | Mostrar a mensagem |
| 409 | `account_link_required` | E-mail já cadastrado sem Google (login) | Levar ao login com senha e depois vincular |
| 409 | `google_account_in_use` | Conta Google já é de outro usuário (vincular) | Mostrar a mensagem |
| 409 | `already_linked` | Usuário já tem outra conta Google (vincular) | Mostrar a mensagem |
| 503 | `google_unavailable` | Google fora do ar ao validar | Tentar de novo depois |
| 503 | `google_auth_unavailable` | `GOOGLE_CLIENT_ID` não configurado | Esconder o botão do Google |

### Notas para o frontend

- Carregue `https://accounts.google.com/gsi/client`, chame `google.accounts.id.initialize({ client_id, callback })`
  e envie `response.credential` para `/api/auth/google/` (ou `/link/`, com o Bearer do Fluxo).
- Guarde e use só o `access`/`refresh` devolvidos pelo backend, exatamente como no login por senha.
- Se a página tiver `Cross-Origin-Opener-Policy`, use `same-origin-allow-popups` para o popup do Google funcionar.

### Produção

Com `DEBUG=False` o backend exige HTTPS: redireciona `http` → `https` (respeitando `X-Forwarded-Proto` do proxy),
cookies só por HTTPS e HSTS (`SECURE_HSTS_SECONDS`, padrão 3600; aumente para `31536000` depois de validar).
Mantenha `CORS_ALLOWED_ORIGINS` só com as origens oficiais do frontend e `CORS_ALLOW_ALL_ORIGINS` desligado.

### Foto de perfil vinda do Google

No login e ao vincular, a foto da conta Google (`picture` do token) vira a foto de perfil **se a pessoa não tiver
escolhido outra no Fluxo**. Ela é baixada uma vez (256px, só JPEG/PNG de `https://*.googleusercontent.com`, sem
seguir redirecionamentos, até `MAX_UPLOAD_SIZE`) e guardada como a foto enviada pelo uploader, então aparece em
todo lugar que já mostra foto. Se o Google mudar a foto, ela é atualizada no próximo login — a não ser que a pessoa
tenha enviado outra. Se a pessoa remover a foto do Google, ela só volta quando o Google tiver uma foto nova.
Falha ao baixar nunca impede o login.
