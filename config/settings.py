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
# 'Africa/Dakar' est UTC+0 toute l'année (pas d'heure d'été) — donc
# identique à 'UTC' pour toute logique métier. On force 'UTC' ici parce
# que MySQL a besoin de ses tables de fuseaux horaires (mysql.time_zone*)
# pour résoudre un nom comme 'Africa/Dakar' dans CONVERT_TZ(), et ces
# tables ne sont PAS chargées par défaut (constaté sur cette installation
# WAMP : mysql.time_zone_name est vide). Sans ça, CONVERT_TZ() renvoie
# NULL pour toute ligne, et TOUT filtre Django sur une date de champ
# datetime (date_heure__date=..., __gte, __lte — utilisés dans les
# rapports) silencieusement ne matche plus rien, sans la moindre erreur.
# 'UTC' est le seul fuseau que Django ne convertit jamais côté SQL
# (la donnée est déjà stockée en UTC quand USE_TZ=True), donc ce bug ne
# peut pas se reproduire, sur aucun serveur MySQL, avec ou sans tables
# de fuseaux chargées.
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

TOKEN_EXPIRE_DAYS = config('TOKEN_EXPIRE_DAYS', default=14, cast=int)

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'superette.authentication.ExpiringTokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '300/minute',
        'connexion': '10/minute',
    },
    'DEFAULT_PAGINATION_CLASS': 'superette.pagination.StandardPagination',
}

# --- Sécurité HTTP & Navigateurs ---
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# --- CORS ---
# En local (DEBUG=True) : tout autorisé, comme avant, pour ne rien
# changer à ton confort de développement.
# En production (DEBUG=False) : SEULS les domaines listés dans
# CORS_ALLOWED_ORIGINS (variable d'environnement, à définir sur
# Railway avec l'adresse Firebase de ton app) peuvent appeler l'API.
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())

CORS_ALLOW_HEADERS = list(default_headers) + [
    "ngrok-skip-browser-warning",
]