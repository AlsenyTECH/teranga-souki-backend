# fichier: superette/management/commands/nettoyer_test.py
#
# Efface TOUTES les données métier (ventes, stock, clients, fournisseurs,
# caisses, dépenses...) et les comptes de test (test_*), pour repartir d'une
# base propre avant la vraie mise en service. Les autres comptes (patron,
# admins réels) et les rôles sont conservés.
#
# Garde-fou : refuse si une opération a été enregistrée par un compte qui
# n'est PAS un compte de test — signe que la base contient de vraies données.
#
#   python manage.py nettoyer_test --oui

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from superette.models import (
    AjustementStock, Approvisionnement, ArchiveCreanceClient, ArchiveCreanceFournisseur, Categorie, Client,
    Depense, Fournisseur, LigneAppro, LigneRetour, LigneRetourAppro, LigneVente, PaiementFournisseur,
    PaiementVente, PrestationService, Produit, RemboursementCredit, RetourAppro, RetourVente, Service,
    SessionCaisse, TransactionCaisse, Utilisateur, VenteProduits,
)

PREFIXE_COMPTE = "test_"

# Ordre de suppression : des tables qui référencent vers les tables référencées
# (les clés étrangères sont en PROTECT, l'ordre compte).
MODELES = [
    LigneRetour, RetourVente, LigneRetourAppro, RetourAppro,
    PaiementVente, LigneVente, VenteProduits, PrestationService, TransactionCaisse,
    LigneAppro, Approvisionnement,
    RemboursementCredit, PaiementFournisseur, ArchiveCreanceClient, ArchiveCreanceFournisseur,
    AjustementStock, Depense, SessionCaisse,
    Produit, Categorie, Service, Client, Fournisseur,
]

# Modèles portant un utilisateur : sert au garde-fou "données réelles".
AVEC_UTILISATEUR = [
    TransactionCaisse, RetourVente, RetourAppro, SessionCaisse, AjustementStock, RemboursementCredit,
    PaiementFournisseur, Depense, ArchiveCreanceClient, ArchiveCreanceFournisseur,
]


class Command(BaseCommand):
    help = "Efface toutes les données métier et les comptes test_* (base de test uniquement)."

    def add_arguments(self, parser):
        parser.add_argument("--oui", action="store_true", help="Confirme la suppression définitive.")
        parser.add_argument(
            "--force", action="store_true",
            help="Supprime même si des opérations ont été faites avec des comptes non-test (DANGEREUX).",
        )

    def handle(self, *args, **options):
        if not options["oui"]:
            raise CommandError("Suppression définitive : ajoute --oui pour confirmer.")

        comptes_test = Utilisateur.objects.filter(compte__username__startswith=PREFIXE_COMPTE)
        if not options["force"]:
            reels = {
                modele.__name__: modele.objects.exclude(utilisateur__in=comptes_test).count()
                for modele in AVEC_UTILISATEUR
            }
            reels = {nom: n for nom, n in reels.items() if n}
            if reels:
                detail = ", ".join(f"{nom} : {n}" for nom, n in reels.items())
                raise CommandError(
                    "Des opérations ont été enregistrées par des comptes qui ne sont pas des comptes de test "
                    f"({detail}). La base contient peut-être de vraies données : rien n'a été supprimé. "
                    "Utilise --force seulement si tu es certain que tout peut partir."
                )

        with transaction.atomic():
            bilan = []
            for modele in MODELES:
                n = modele.objects.count()
                modele.objects.all().delete()
                if n:
                    bilan.append(f"{modele.__name__} : {n}")
            n_comptes = User.objects.filter(username__startswith=PREFIXE_COMPTE).count()
            # Le profil Utilisateur (et les jetons) partent avec le compte (CASCADE).
            User.objects.filter(username__startswith=PREFIXE_COMPTE).delete()

        self.stdout.write(self.style.SUCCESS(
            "Base nettoyée.\n  " + ("\n  ".join(bilan) or "aucune donnée métier") +
            f"\n  comptes de test supprimés : {n_comptes}"
        ))
