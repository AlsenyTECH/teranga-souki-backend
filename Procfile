# fichier: Procfile
# À CRÉER — à la racine du projet backend (à côté de manage.py), SANS extension.
#
# release : exécuté automatiquement à CHAQUE déploiement, avant que le
# nouveau code ne serve du trafic — c'est ce qui applique tes migrations
# automatiquement, sans que tu aies à te connecter en SSH à chaque fois.
# web : le vrai serveur de production (gunicorn), qui remplace
# "python manage.py runserver" utilisé en local.

web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi --bind 0.0.0.0:8080 --log-file -
