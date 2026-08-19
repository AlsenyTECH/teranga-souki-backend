# fichier: superette/permissions.py
# Fichier À CRÉER (n'existe pas encore dans superette/)

from rest_framework.permissions import BasePermission


def get_role(request):
    """
    Récupère le rôle métier de l'utilisateur connecté.
    request.user est l'utilisateur Django (auth_user) posé automatiquement
    par TokenAuthentication. On remonte vers notre profil métier via
    la relation inverse du OneToOneField : user.utilisateur
    """
    utilisateur = getattr(request.user, "utilisateur", None)
    if utilisateur is None or not utilisateur.actif:
        return None
    return utilisateur.role_id  # "caissier" ou "admin"


class EstAdmin(BasePermission):
    """Accès réservé au rôle admin uniquement."""

    def has_permission(self, request, view):
        return get_role(request) == "admin"


class EstCaissierOuAdmin(BasePermission):
    """Accès pour caissier ET admin — le cas le plus courant (ex: enregistrer une vente)."""

    def has_permission(self, request, view):
        return get_role(request) in ("caissier", "admin")


class LectureAdminEcritureAdmin(BasePermission):
    """
    Exemple de permission plus fine : tout le monde d'authentifié peut
    lire (GET), mais seul l'admin peut créer/modifier/supprimer.
    Utile pour Produit, Categorie, Fournisseur : le caissier doit
    pouvoir CONSULTER le catalogue en caisse, mais pas le modifier.
    """

    def has_permission(self, request, view):
        role = get_role(request)
        if role is None:
            return False
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True  # lecture : caissier ou admin
        return role == "admin"  # écriture : admin seulement


# ============================================================
# Permissions granulaires par fonctionnalité — un admin peut
# accorder à un caissier l'accès à des rubriques normalement
# réservées (Catalogue, Fournisseurs, Approvisionnement, Clients,
# Dépenses, Rapports), une par une, via Utilisateur.permissions_supplementaires
# (liste de chaînes). Un admin a toujours accès à tout.
# ============================================================

CLES_PERMISSIONS_VALIDES = [
    "catalogue", "fournisseurs", "approvisionnement", "clients", "depenses", "rapports",
]


def _a_permission(request, cle):
    utilisateur = getattr(request.user, "utilisateur", None)
    if utilisateur is None:
        return False
    return cle in (utilisateur.permissions_supplementaires or [])


class _PeutGererFonctionnalite(BasePermission):
    """
    Classe de base : lecture toujours ouverte au caissier (comme
    LectureAdminEcritureAdmin), écriture réservée à l'admin OU à un
    caissier ayant reçu la permission `cle` explicitement.
    """
    cle = None

    def has_permission(self, request, view):
        role = get_role(request)
        if role is None:
            return False
        if role == "admin":
            return True
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return _a_permission(request, self.cle)


class _PeutAccederFonctionnaliteStricte(BasePermission):
    """
    Variante stricte : même la LECTURE est concernée (Approvisionnement,
    Dépenses, Rapports) — un caissier n'y voit rien du tout sans
    permission explicite, contrairement au Catalogue/Fournisseurs/
    Clients où consulter reste normal pour n'importe quel caissier.
    """
    cle = None

    def has_permission(self, request, view):
        role = get_role(request)
        if role is None:
            return False
        if role == "admin":
            return True
        return _a_permission(request, self.cle)


class PeutGererCatalogue(_PeutGererFonctionnalite):
    cle = "catalogue"


class PeutGererFournisseurs(_PeutGererFonctionnalite):
    cle = "fournisseurs"


class PeutGererClients(_PeutGererFonctionnalite):
    cle = "clients"


class PeutGererApprovisionnement(_PeutAccederFonctionnaliteStricte):
    cle = "approvisionnement"


class PeutGererDepenses(_PeutAccederFonctionnaliteStricte):
    cle = "depenses"


class PeutVoirRapports(_PeutAccederFonctionnaliteStricte):
    cle = "rapports"