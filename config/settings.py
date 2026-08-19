# fichier: config/settings.py
# À REMPLACER ENTIÈREMENT — ce fichier fonctionne à la fois en local
# (comme avant, avec ton .env) ET en production sur Railway (avec les
# variables d'environnement que Railway fournit automatiquement).
#
# Rien ne casse ton usage local actuel : DATABASE_URL n'existe que sur
# Railway, donc en local le bloc DATABASES retombe sur tes réglages
# habituels (DB_NAME, DB_USER...).

from pathlib import Path
from decouple import config, Csv
from corsheaders.defaults import default_headers
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
# IMPORTANT : DEBUG=False par défaut. En local, ton .env doit avoir
# DEBUG=True explicitement — en production, on ne veut JAMAIS que
# DEBUG s'active par erreur (ça exposerait du code source aux visiteurs
# en cas d'erreur serveur).
DEBUG = config('DEBUG', default=False, cast=bool)

# En local : localhost/127.0.0.1 suffisent (valeur par défaut).
# En production : Railway fournit RAILWAY_PUBLIC_DOMAIN automatiquement
# — on l'ajoute à la liste sans que tu aies à la configurer à la main.
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())
railway_domain = config('RAILWAY_PUBLIC_DOMAIN', default='')
if railway_domain:
    ALLOWED_HOSTS.append(railway_domain)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'superette',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',       # doit être avant CommonMiddleware
    'django.middleware.security.SecurityMiddleware',
    # whitenoise sert les fichiers statiques (CSS/JS de l'admin Django)
    # directement depuis Django en production — pas besoin d'un serveur
    # séparé juste pour ça, sur une app de cette taille.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

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

WSGI_APPLICATION = 'config.wsgi.application'

# --- Base de données ---
# DATABASE_URL n'existe QUE sur Railway (il l'injecte automatiquement
# dès qu'un service MySQL est attaché au projet) — en local, cette
# variable est absente, donc on retombe sur ta config MySQL habituelle.
DATABASE_URL = config('DATABASE_URL', default='')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': config('DB_NAME'),
            'USER': config('DB_USER'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST'),
            'PORT': config('DB_PORT'),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Dakar'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# --- CORS ---
# En local (DEBUG=True) : tout autorisé, comme avant, pour ne rien
# changer à ton confort de développement.
# En production (DEBUG=False) : SEULS les domaines listés dans
# CORS_ALLOWED_ORIGINS (variable d'environnement, à définir sur
# Railway avec l'adresse Firebase de ton app) peuvent appeler l'API —
# c'est le correctif de sécurité qu'on avait repéré et mis de côté
# quand on a construit l'app ("ne jamais laisser CORS_ALLOW_ALL_ORIGINS
# = True en production").
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())

CORS_ALLOW_HEADERS = list(default_headers) + [
    "ngrok-skip-browser-warning",
]