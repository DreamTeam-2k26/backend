import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from app.env import env_bool, env_list

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# Constrói o caminho base do projeto, usado para definir caminhos relativos
BASE_DIR = Path(__file__).resolve().parent.parent

TESTING = 'test' in sys.argv

# Segurança e configuração básica
DEBUG = env_bool('DEBUG')
SECRET_KEY = os.getenv('SECRET_KEY')

if not SECRET_KEY:
    if not (DEBUG or TESTING):
        raise ImproperlyConfigured('Defina SECRET_KEY no .env (obrigatória com DEBUG=False).')
    SECRET_KEY = 'django-insecure-somente-para-desenvolvimento'

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', 'http://localhost:3000,http://localhost:8000')
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', 'http://localhost:3000')
CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL_ORIGINS', default=DEBUG)

# Produção (DEBUG=False) só por HTTPS. O Fabroku termina o TLS no proxy e informa o esquema original
# em X-Forwarded-Proto. HSTS começa curto; aumente SECURE_HSTS_SECONDS (ex.: 31536000) depois de validar.
if not (DEBUG or TESTING):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '3600'))

# Login com Google (Google Identity Services). Client ID do tipo "Aplicativo da Web" no Google Cloud; é
# público (o frontend usa o mesmo). Não existe client secret neste fluxo. Vazio: /api/auth/google/ responde
# 503 e o `manage.py check` avisa (core.W001) fora do DEBUG.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()

# IA para o resumo de reuniões e a síntese do Brainstorm (API de chat compatível com a OpenAI; padrão: OpenRouter
# com um modelo gratuito). Chave só no servidor; vazia, as ações de IA respondem 503 e o resto funciona normal.
# Sem AI_ALLOW_PAID=true, core.ai recusa qualquer modelo que não seja gratuito (`:free` ou `openrouter/free`).
AI_API_KEY = os.getenv('AI_API_KEY', '').strip()
AI_API_URL = os.getenv('AI_API_URL', '').strip() or 'https://openrouter.ai/api/v1/chat/completions'
AI_MODEL = os.getenv('AI_MODEL', '').strip() or 'google/gemma-4-31b-it:free'
AI_ALLOW_PAID = os.getenv('AI_ALLOW_PAID', '').strip().lower() in {'1', 'true', 'yes'}
# Reservas (vírgula) tentadas em ordem quando o modelo principal falha; modelos gratuitos ficam congestionados.
AI_FALLBACK_MODELS = [
    model.strip()
    for model in (os.getenv('AI_FALLBACK_MODELS') or 'nvidia/nemotron-3-ultra-550b-a55b:free').split(',')
    if model.strip()
]
BRAINSTORM_AI_MODEL = os.getenv('BRAINSTORM_AI_MODEL', '').strip() or AI_MODEL

# Aplicações instaladas
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cloudinary_storage',
    'cloudinary',
    'corsheaders',
    'channels',
    'django_extensions',
    'django_filters',
    'drf_spectacular',
    'rest_framework',
    'rest_framework_simplejwt',
    'uploader',
    'core',
    'chat',
    'calls',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'app.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'app.wsgi.application'
ASGI_APPLICATION = 'app.asgi.application'

# Configuração do Redis / Django Channels
# Sem REDIS_URL usa a camada em memória, que só funciona com um único processo.
REDIS_URL = os.getenv('REDIS_URL')

if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [
                    {
                        'address': REDIS_URL,
                        'socket_timeout': None,
                    }
                ]
            },
        }
    }
else:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# Banco de dados
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=0,
        conn_health_checks=True,
    )
}

# Validação de senhas
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Hashing rápido durante os testes (PBKDF2 deixa a suíte lenta)
if TESTING:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Configurações de internacionalização
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Configurações de arquivos estáticos
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Configurações de arquivos de mídia (App Uploader)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
FILE_UPLOAD_PERMISSIONS = 0o640
MAX_UPLOAD_SIZE = int(os.getenv('MAX_UPLOAD_SIZE_MB', '5')) * 1024 * 1024

# Tamanho máximo dos anexos do chat (qualquer tipo de arquivo; o download exige participar da sala).
CHAT_MAX_UPLOAD_MB = int(os.getenv('CHAT_MAX_UPLOAD_MB', '10'))

# Tamanho máximo dos arquivos de projeto (core.File; tipos permitidos em core/models/file.py).
PROJECT_FILE_MAX_UPLOAD_MB = int(os.getenv('PROJECT_FILE_MAX_UPLOAD_MB', '20'))

# Com CLOUDINARY_URL definida os uploads vão para o Cloudinary; sem ela ficam em MEDIA_ROOT.
# Fora do DEBUG os estáticos usam o manifest do whitenoise (exige collectstatic).
CLOUDINARY_URL = os.getenv('CLOUDINARY_URL')
STORAGES = {
    'default': {
        'BACKEND': (
            'cloudinary_storage.storage.MediaCloudinaryStorage'
            if CLOUDINARY_URL
            else 'django.core.files.storage.FileSystemStorage'
        ),
    },
    'staticfiles': {
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage'
            if DEBUG or TESTING
            else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}

# Tipo padrão de campo para chaves primárias
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Configurações do DRF e drf-spectacular (OpenAPI/Swagger)
SPECTACULAR_SETTINGS = {
    'TITLE': 'Fluxo API',
    'DESCRIPTION': 'API do Fluxo, plataforma colaborativa de gerenciamento de projetos e tarefas.',
    'VERSION': '1.0.0',
    # Membership e Invite têm campos 'role' com opções diferentes (convite não cria dono).
    'ENUM_NAME_OVERRIDES': {
        'MembershipRoleEnum': 'core.models.project.Membership.Role',
        'InviteRoleEnum': 'core.models.invite.Invite.ROLE_CHOICES',
        # status/category do Brainstorm não podem colidir com os de tarefas, colunas, convites e chamadas.
        'BrainstormTopicStatusEnum': 'core.models.brainstorm_topic.BrainstormTopic.Status',
        'BrainstormTopicCategoryEnum': 'core.models.brainstorm_topic.BrainstormTopic.Category',
        'BrainstormAnalysisStatusEnum': 'core.models.brainstorm_analysis.BrainstormAnalysis.Status',
        'CategoryEnum': 'core.models.column.Column.Category',
    },
}

# Modelo de usuário personalizado
AUTH_USER_MODEL = 'core.User'

# Configurações do Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
        'core.permissions.ModelPermissions',
    ),
    'DEFAULT_PAGINATION_CLASS': 'app.pagination.CustomPagination',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# Configurações do Simple JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=180),  # Tokens de acesso expiram em 3 horas
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),  # Tokens de atualização expiram em 1 dia
    'AUTH_HEADER_TYPES': ('Bearer',),
}
