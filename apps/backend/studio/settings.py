import json
import os

from django.core.exceptions import ImproperlyConfigured

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-studio-development-only")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(",")
ROOT_URLCONF = "studio.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
STATIC_URL = "/static/"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "scheduler",
    "production",
    "media",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "studio",
        "USER": "studio",
        "PASSWORD": os.getenv("STUDIO_DB_PASSWORD", "studio-local"),
        "HOST": os.getenv("STUDIO_DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("STUDIO_DB_PORT", "5432"),
        "OPTIONS": {"connect_timeout": 3},
    },
}
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Short Drama Studio API",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
}

WORKFLOW_SERVICE_TOKEN = os.getenv("STUDIO_WORKFLOW_SERVICE_TOKEN", "")
WORKFLOW_VERIFIER_TOKEN = os.getenv("STUDIO_WORKFLOW_VERIFIER_TOKEN", "")
N8N_DISPATCH_TOKEN = os.getenv("STUDIO_N8N_DISPATCH_TOKEN", "")
try:
    N8N_WEBHOOKS = json.loads(os.getenv("STUDIO_N8N_WEBHOOKS") or "{}")
except ValueError as exc:
    raise ImproperlyConfigured("STUDIO_N8N_WEBHOOKS must be a JSON object") from exc
if not isinstance(N8N_WEBHOOKS, dict):
    raise ImproperlyConfigured("STUDIO_N8N_WEBHOOKS must map workflow names to webhook URLs")
SCHEDULER_WORKFLOW_DISPATCHER = "studio.n8n.dispatch_workflow" if N8N_WEBHOOKS else None

SCHEDULER_TASK_CLASSES = (
    "production.tasks.ShortDrama",
    "production.tasks.GlobalAssets",
    "production.tasks.AllEpisodes",
    "production.tasks.MakeEpisode",
    "production.tasks.EpisodeAssets",
    "production.tasks.AllShotImages",
    "production.tasks.AllShots",
    "production.tasks.MakeShot",
    "production.leaves.SeriesPlan",
    "production.leaves.EpisodeScript",
    "production.leaves.EpisodeBreakdown",
    "production.leaves.CharacterAsset",
    "production.leaves.SceneAsset",
    "production.leaves.PropAsset",
    "production.leaves.ShotImage",
    "production.leaves.ShotDialogue",
    "production.leaves.ShotVideo",
    "production.leaves.ShotSound",
    "production.leaves.EpisodeEdit",
)
