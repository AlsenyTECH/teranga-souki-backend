# fichier: superette/authentication.py
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ExpiringTokenAuthentication(TokenAuthentication):
    """
    Authentification par token avec vérification d'expiration.
    Si le token a été créé il y a plus de TOKEN_EXPIRE_DAYS jours
    (par défaut 14 jours), l'authentification échoue avec une exception
    qui renvoie un code HTTP 401 Unauthorized à l'application cliente.
    """

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.select_related('user').get(key=key)
        except model.DoesNotExist:
            raise AuthenticationFailed("Token invalide ou inexistant.")

        if not token.user.is_active:
            raise AuthenticationFailed("Cet utilisateur a été désactivé.")

        # Vérification de l'expiration du token
        expire_days = getattr(settings, 'TOKEN_EXPIRE_DAYS', 14)
        if token.created < timezone.now() - timedelta(days=expire_days):
            # Supprime le token expiré pour libérer de l'espace et forcer la regénération
            token.delete()
            raise AuthenticationFailed("Session expirée. Veuillez vous reconnecter.")

        return (token.user, token)
