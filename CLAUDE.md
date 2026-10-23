# CLAUDE.md — Backend do Fluxo

Backend Django + DRF do **Fluxo**. Siga o padrão do tutorial **Django com DRF (2026)** do prof. Marco André
(https://github.com/marrcandre/django-drf-tutorial). O código de `core/` (projetos, usuários) já segue esse padrão:
use-o como referência.

## Arquitetura: tudo no app `core`

- Código novo de domínio vai em `core/`. **Não crie apps Django novos** e **não crie camada de services**.
  (`chat/` e `uploader/` já existem separados; não mexa neles sem pedido.)
- Fluxo de toda API: **Model → Serializer → ViewSet → Router → URL**.
- Um arquivo por entidade em cada pasta, exportado no `__init__.py`:

| O quê | Arquivo | Exportar em |
|---|---|---|
| Model | `core/models/<entidade>.py` | `core/models/__init__.py` |
| Serializers | `core/serializers/<entidade>.py` | `core/serializers/__init__.py` |
| ViewSet | `core/views/<entidade>.py` | `core/views/__init__.py` |
| Permissão de objeto | `core/permissions.py` | — |
| Rota | `router.register(...)` em `app/urls.py` | — |
| Admin | `core/admin.py` | — |
| Testes | `core/tests/test_<entidade>_*.py` | — |

- Nos `__init__.py`, mantenha o estilo `from .x import A, B` (o ruff ignora esses arquivos; não rode `--fix` neles).

## Model

- Classe no singular; campos no singular. `__str__` sempre.
- `ForeignKey` com `on_delete` pensado: `CASCADE` (filho some com o pai), `PROTECT` (bloqueia), `SET_NULL` +
  `null=True, blank=True` (histórico, ex.: `created_by`). Sempre `related_name`.
- Opções fixas com `models.TextChoices`/`IntegerChoices` dentro da model (ex.: `Membership.Role`).
- Unicidade com `Meta.constraints = [models.UniqueConstraint(...)]`.
- Datas: `created_at = DateTimeField(auto_now_add=True)`, `updated_at = DateTimeField(auto_now=True)`.
- Depois de mudar model: `pdm makemigrations` + `pdm migrate`.

## Serializers: um por ação

```python
from rest_framework.serializers import CharField, ModelSerializer

class XListSerializer(ModelSerializer):          # list: só o que a tela de lista precisa
class XRetrieveSerializer(ModelSerializer):      # retrieve: tudo (pode usar depth ou serializers aninhados)
class XCreateUpdateSerializer(ModelSerializer):  # create/update: só campos graváveis
```

- `fields` sempre explícito (tupla); nada de `'__all__'` em código novo.
- Campo exibido mas não gravável: `read_only=True` (ex.: `CharField(source='usuario.email', read_only=True)`).
- Gravação em mais de uma tabela (ex.: projeto + membership do dono, itens aninhados): sobrescreva `create`/`update`
  no serializer com `@transaction.atomic`.
- Validação: `validate_<campo>` e `validate(self, attrs)`, levantando `ValidationError` com mensagem em português.
- Ação customizada com entrada própria: serializer dedicado (`Serializer`) para validar a entrada.

## ViewSet

```python
class XViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, XPermission]

    def get_queryset(self):            # SEMPRE restringe ao que o usuário pode ver
        ...

    def get_serializer_class(self):
        if self.action == 'list':
            return XListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return XCreateUpdateSerializer
        return XRetrieveSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)   # autor vem do token, nunca do corpo
```

- Exemplo real: `core/views/project.py`.
- Ações extras: `@action(detail=True|False, methods=[...])` + `@extend_schema(summary=..., request=..., responses=...)`.
- Filtros/busca/ordenação: `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter` com `filterset_fields`,
  `search_fields`, `ordering_fields`, `ordering`.
- Rota: `router.register(r'<plural>', XViewSet, basename='<plural>')` em `app/urls.py`.

## Autorização (regra do Fluxo)

- Recursos de projeto são autorizados pela **participação** (`Membership`), não pelos grupos do Django:
  use `IsAuthenticated` + uma permissão de objeto em `core/permissions.py` (ex.: `ProjectRolePermission`)
  em vez do `ModelPermissions` padrão.
- `get_queryset` filtra pelos projetos do usuário → quem não participa recebe **404**; quem participa sem papel
  suficiente recebe **403**.
- Papéis: dono (`owner`), administrador (`admin`), membro (`member`). Ver `contexto.md` na raiz do workspace.
- Nunca confie em ids de autor/usuário vindos do frontend.

## Admin

- `@admin.register(X)` com `list_display`, `search_fields`, `list_filter`, `ordering` quando fizer sentido.
- Filhos editáveis no pai com `TabularInline` (ex.: memberships dentro do projeto).

## Testes e verificação

- `APITestCase` em `core/tests/`, helpers em `core/tests/helpers.py` (`make_user`, `make_project`, `client_for`).
- Cubra: sem login (401), fora do projeto (404), papel errado (403), caminho feliz, validação (400).
- Antes de dizer que terminou:
  - `pdm test` (suíte inteira)
  - `pdm run python manage.py makemigrations --check --dry-run`
  - `pdm lint`

## Commits

Mensagens com prefixo, como no tutorial: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `style:`.
Não faça commit, push ou merge sem pedido explícito.
