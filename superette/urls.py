# fichier: superette/urls.py
# Fichier À CRÉER (n'existe pas encore dans superette/)

from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import (
    CategorieViewSet, ProduitViewSet, ClientViewSet,
    FournisseurViewSet, ServiceViewSet, ConnexionView, VenteView,
    ApprovisionnementView, PrestationView, DepenseViewSet, RapportJournalierView,
    RapportPeriodeView, RoleViewSet, UtilisateurListCreateView,
    UtilisateurPermissionsView, MonProfilView,
    AjustementStockView, AnnulerVenteView, RemboursementCreditView,
)

# DefaultRouter génère automatiquement toutes les URLs REST standard
# pour chaque ViewSet enregistré : /produits/, /produits/1/, etc.
router = DefaultRouter()
router.register("categories", CategorieViewSet)
router.register("produits", ProduitViewSet)
router.register("clients", ClientViewSet)
router.register("fournisseurs", FournisseurViewSet)
router.register("services", ServiceViewSet)
router.register("depenses", DepenseViewSet)
router.register("roles", RoleViewSet)

urlpatterns = [
    path("connexion/", ConnexionView.as_view(), name="connexion"),
    path("ventes/", VenteView.as_view(), name="ventes"),
    path("ventes/<int:pk>/annuler/", AnnulerVenteView.as_view(), name="annuler-vente"),
    path("approvisionnements/", ApprovisionnementView.as_view(), name="approvisionnements"),
    path("prestations/", PrestationView.as_view(), name="prestations"),
    path("rapports/", RapportJournalierView.as_view(), name="rapports"),
    path("rapports/periode/", RapportPeriodeView.as_view(), name="rapport-periode"),
    path("utilisateurs/", UtilisateurListCreateView.as_view(), name="utilisateurs"),
    path("utilisateurs/<int:pk>/permissions/", UtilisateurPermissionsView.as_view(), name="utilisateur-permissions"),
    path("mon-profil/", MonProfilView.as_view(), name="mon-profil"),
    path("ajustements-stock/", AjustementStockView.as_view(), name="ajustements-stock"),
    path("remboursements-credit/", RemboursementCreditView.as_view(), name="remboursements-credit"),
    path("", include(router.urls)),
]