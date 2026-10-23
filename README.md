# Fluxo — Backend

API REST do **Fluxo** com Django, Django REST Framework, JWT, Channels (WebSocket) e upload de arquivos, gerenciada com [PDM](https://pdm-project.org).

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
- Sem `DATABASE_URL` usa SQLite; sem `REDIS_URL` usa a camada do Channels em memória; sem `CLOUDINARY_URL` grava uploads em `media/`.

## Estrutura

- `app/` — configuração do projeto (settings, urls, asgi, paginação)
- `core/` — usuário customizado (login por e-mail), grupos, permissões, JWT e middleware de WebSocket
- `uploader/` — upload de imagens (JPEG/PNG) e documentos (PDF)

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
