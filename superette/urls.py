# fichier: superette/urls.py
# Fichier À CRÉER (n'existe pas encore dans superette/)

from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import (
    CategorieViewSet, ProduitViewSet, ClientViewSet,
    FournisseurViewSet, ServiceViewSet, ConnexionView, VenteView,
    ApprovisionnementView, ApprovisionnementDetailView, RetourApproView, PrestationView, DepenseViewSet, RapportJournalierView,
    RapportPeriodeView, RoleViewSet, UtilisateurListCreateView,
    UtilisateurPermissionsView, UtilisateurDetailView, MonProfilView,
    AjustementStockView, AnnulerVenteView, RemboursementCreditView, PaiementFournisseurView,
    SessionCaisseOuvrirView, SessionCaisseCouranteView, SessionCaisseFermerView, SessionCaisseListView,
    RetourVenteView, ArchiverCreanceClientView, ArchiverCreanceFournisseurView,
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
    path("ventes/<int:pk>/retour/", RetourVenteView.as_view(), name="retour-vente"),
    path("approvisionnements/", ApprovisionnementView.as_view(), name="approvisionnements"),
    path("approvisionnements/<int:pk>/", ApprovisionnementDetailView.as_view(), name="approvisionnement-detail"),
    path("approvisionnements/<int:pk>/retour/", RetourApproView.as_view(), name="retour-appro"),
    path("prestations/", PrestationView.as_view(), name="prestations"),
    path("rapports/", RapportJournalierView.as_view(), name="rapports"),
    path("rapports/periode/", RapportPeriodeView.as_view(), name="rapport-periode"),
    path("utilisateurs/", UtilisateurListCreateView.as_view(), name="utilisateurs"),
    path("utilisateurs/<int:pk>/", UtilisateurDetailView.as_view(), name="utilisateur-detail"),
    path("utilisateurs/<int:pk>/permissions/", UtilisateurPermissionsView.as_view(), name="utilisateur-permissions"),
    path("mon-profil/", MonProfilView.as_view(), name="mon-profil"),
    path("ajustements-stock/", AjustementStockView.as_view(), name="ajustements-stock"),
    path("remboursements-credit/", RemboursementCreditView.as_view(), name="remboursements-credit"),
    path("paiements-fournisseur/", PaiementFournisseurView.as_view(), name="paiements-fournisseur"),
    path("clients/<int:pk>/archiver-creance/", ArchiverCreanceClientView.as_view(), name="archiver-creance-client"),
    path("fournisseurs/<int:pk>/archiver-creance/", ArchiverCreanceFournisseurView.as_view(), name="archiver-creance-fournisseur"),
    path("sessions-caisse/", SessionCaisseListView.as_view(), name="sessions-caisse"),
    path("sessions-caisse/ouvrir/", SessionCaisseOuvrirView.as_view(), name="session-caisse-ouvrir"),
    path("sessions-caisse/courante/", SessionCaisseCouranteView.as_view(), name="session-caisse-courante"),
    path("sessions-caisse/<int:pk>/fermer/", SessionCaisseFermerView.as_view(), name="session-caisse-fermer"),
    path("", include(router.urls)),
]