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
#
# Après « peupler_test --catalogue-existant » (un instantané existe) : seul
# ce qui a été créé APRÈS l'instantané est effacé — données de test ET essais
# faits dans l'app pendant la période de test, quel que soit le compte —, puis
# le stock et le prix d'achat moyen des produits, et les soldes des clients /
# fournisseurs réels, reprennent leur valeur d'avant. Les produits et
# catégories (y compris ceux ajoutés pendant les tests) et les comptes réels
# sont conservés.

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from superette.models import (
    AjustementStock, Approvisionnement, ArchiveCreanceClient, ArchiveCreanceFournisseur, Categorie, Client,
    Depense, Fournisseur, InstantaneDonneesTest, LigneAppro, LigneRetour, LigneRetourAppro, LigneVente, PaiementFournisseur,
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
        parser.add_argument(
            "--tout", action="store_true",
            help="Remise à zéro complète : efface TOUTES les données métier (produits compris), même après "
                 "--catalogue-existant et même celles saisies avec les vrais comptes. Les comptes réels restent.",
        )

    def handle(self, *args, **options):
        if not options["oui"]:
            raise CommandError("Suppression définitive : ajoute --oui pour confirmer.")
        instantane = InstantaneDonneesTest.objects.order_by("pk").first()
        if instantane is not None and not options["tout"]:
            return self.restaurer(instantane)

        comptes_test = Utilisateur.objects.filter(compte__username__startswith=PREFIXE_COMPTE)
        if not (options["force"] or options["tout"]):
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
            InstantaneDonneesTest.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            "Base nettoyée.\n  " + ("\n  ".join(bilan) or "aucune donnée métier") +
            f"\n  comptes de test supprimés : {n_comptes}"
        ))

    def restaurer(self, instantane):
        """Nettoyage après « peupler_test --catalogue-existant »."""
        donnees = instantane.donnees
        reperes = donnees["reperes"]
        with transaction.atomic():
            bilan = []
            for modele in MODELES:
                if modele in (Produit, Categorie):
                    continue  # le catalogue reste, seul son état est restauré
                nouveaux = modele.objects.filter(pk__gt=reperes.get(modele.__name__, 0))
                n = nouveaux.count()
                nouveaux.delete()
                if n:
                    bilan.append(f"{modele.__name__} : {n}")

            anciens = donnees["produits"]
            for produit in Produit.objects.select_for_update():
                etat = anciens.get(str(produit.pk))
                # Produit ajouté pendant les tests : on le garde, sans stock.
                produit.quantite_stock = Decimal(etat["quantite_stock"]) if etat else Decimal("0")
                produit.prix_achat_moyen = Decimal(etat["prix_achat_moyen"]) if etat else Decimal("0")
                produit.save(update_fields=["quantite_stock", "prix_achat_moyen"])
            for pk, etat in donnees["clients"].items():
                Client.objects.filter(pk=pk).update(
                    solde_credit=Decimal(etat["solde_credit"]), points_fidelite=etat["points_fidelite"])
            for pk, etat in donnees["fournisseurs"].items():
                Fournisseur.objects.filter(pk=pk).update(solde_du=Decimal(etat["solde_du"]))

            # Produits du catalogue de test ajoutés pour compléter le vrai.
            produits_test = Produit.objects.filter(pk__in=donnees.get("produits_test", []))
            n_produits_test = produits_test.count()
            produits_test.delete()
            n_categories = Categorie.objects.filter(
                pk__gt=reperes.get("Categorie", 0), produit__isnull=True).delete()[0]
            if n_produits_test:
                bilan.append(f"produits de test : {n_produits_test} (catégories vides : {n_categories})")

            n_comptes = User.objects.filter(username__startswith=PREFIXE_COMPTE).count()
            User.objects.filter(username__startswith=PREFIXE_COMPTE).delete()
            InstantaneDonneesTest.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f"Données de test effacées (instantané du {instantane.date_creation:%d/%m/%Y %H:%M}).\n  "
            + ("\n  ".join(bilan) or "aucune donnée créée depuis l'instantané")
            + f"\n  comptes de test supprimés : {n_comptes}"
            + f"\n  {Produit.objects.count()} produits conservés, stock et prix d'achat remis à leur état d'avant."
        ))
