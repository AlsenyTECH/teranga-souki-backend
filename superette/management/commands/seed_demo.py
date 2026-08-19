# fichier: superette/management/commands/seed_demo.py
# Fichier À CRÉER — nécessite aussi ces deux fichiers vides à côté :
#   superette/management/__init__.py
#   superette/management/commands/__init__.py
# (Django exige ces __init__.py pour reconnaître le dossier comme un
# module de commandes — sans eux, "seed_demo" resterait invisible.)
#
# Lancement : python manage.py seed_demo
# Idempotent pour les données de référence (catégories, produits,
# fournisseurs, clients, comptes) via get_or_create — tu peux la
# relancer sans dupliquer ces éléments. Les ventes/réceptions de
# démonstration, elles, ne sont créées qu'une seule fois (si aucune
# transaction n'existe déjà), pour ne pas s'accumuler à chaque essai.

from decimal import Decimal
import random

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from superette.models import (
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur, Service,
    TransactionCaisse, VenteProduits, LigneVente, Approvisionnement, LigneAppro,
    Depense,
)


class Command(BaseCommand):
    help = "Remplit la base avec des données de démonstration pour Teranga Souki."

    def handle(self, *args, **options):
        self.stdout.write("Rôles...")
        role_admin, _ = Role.objects.get_or_create(libelle="admin")
        role_caissier, _ = Role.objects.get_or_create(libelle="caissier")

        self.stdout.write("Comptes...")
        admin_user, cree = User.objects.get_or_create(
            username="admin1", defaults={"password": make_password("admin1234")})
        if cree:
            admin_user.set_password("admin1234")
            admin_user.save()
        utilisateur_admin, _ = Utilisateur.objects.get_or_create(
            compte=admin_user,
            defaults={"nom": "Fatou Ndiaye", "telephone": "770000001", "role": role_admin, "actif": True},
        )

        caissier_user, cree = User.objects.get_or_create(
            username="caissier1", defaults={"password": make_password("caissier1234")})
        if cree:
            caissier_user.set_password("caissier1234")
            caissier_user.save()
        utilisateur_caissier, _ = Utilisateur.objects.get_or_create(
            compte=caissier_user,
            defaults={"nom": "Moussa Diop", "telephone": "770000002", "role": role_caissier, "actif": True},
        )

        self.stdout.write("Catégories et produits...")
        categories_data = {
            "Boissons": [("Coca-Cola 33cl", 500), ("Eau minérale 1.5L", 500), ("Jus Vimto 1L", 1500)],
            "Épicerie": [("Riz brisé 5kg", 4500), ("Huile Diamaraf 1L", 1800), ("Sucre 1kg", 800), ("Lait en poudre 400g", 2500)],
            "Hygiène": [("Savon Palmida", 400), ("Dentifrice Signal", 1200), ("Papier hygiénique x4", 1000)],
            "Snacks": [("Biscuits Choco", 300), ("Chips Sila", 500), ("Bonbons assortis", 100)],
            "Divers": [("Bougie", 200), ("Allumettes", 100), ("Pile AA x2", 600)],
        }
        produits_crees = []
        for libelle, items in categories_data.items():
            categorie, _ = Categorie.objects.get_or_create(libelle=libelle)
            for nom, prix in items:
                produit, _ = Produit.objects.get_or_create(
                    nom=nom, categorie=categorie,
                    defaults={
                        "prix_vente": Decimal(prix),
                        "prix_achat_moyen": (Decimal(prix) * Decimal("0.7")).quantize(Decimal("0.01")),
                        "quantite_stock": random.randint(15, 60),
                        "seuil_alerte": 10,
                    },
                )
                produits_crees.append(produit)
        # Deux produits volontairement sous leur seuil, pour tester l'alerte stock bas
        for p in produits_crees[:2]:
            p.quantite_stock = random.randint(1, 4)
            p.save(update_fields=["quantite_stock"])

        self.stdout.write("Fournisseurs...")
        fournisseurs = []
        for nom, contact in [
            ("Grossiste Sandaga", "771111111"),
            ("Distributeur Sococim", "772222222"),
            ("Fournisseur Boissons Sénégal", "773333333"),
        ]:
            f, _ = Fournisseur.objects.get_or_create(nom=nom, defaults={"contact": contact})
            fournisseurs.append(f)

        self.stdout.write("Clients...")
        clients = []
        for nom, tel in [
            ("Aissatou Ba", "774444444"), ("Ibrahima Fall", "775555555"),
            ("Mariama Sow", "776666666"), ("Cheikh Gueye", "777777777"),
        ]:
            c, _ = Client.objects.get_or_create(nom=nom, defaults={"telephone": tel, "points_fidelite": random.randint(0, 50)})
            clients.append(c)

        self.stdout.write("Service de déplumage...")
        Service.objects.get_or_create(libelle="Déplumage de poulet", defaults={"tarif": Decimal("500")})

        self.stdout.write("Dépenses...")
        for montant, categorie, recurrence in [
            (25000, "Loyer", "mensuelle"), (5000, "Électricité", "mensuelle"),
            (2000, "Transport marchandise", "ponctuelle"), (1500, "Sachets plastique", "hebdomadaire"),
        ]:
            Depense.objects.get_or_create(
                montant=Decimal(montant), categorie=categorie, date_depense=timezone.localdate(),
                defaults={"type_recurrence": recurrence, "utilisateur": utilisateur_admin, "description": ""},
            )

        if not TransactionCaisse.objects.exists():
            self.stdout.write("Ventes de démonstration...")
            for _ in range(8):
                choix = random.sample(produits_crees, k=random.randint(1, 3))
                montant_total = Decimal("0")
                lignes = []
                for p in choix:
                    qte = random.randint(1, 3)
                    montant_total += p.prix_vente * qte
                    lignes.append((p, qte))
                transaction = TransactionCaisse.objects.create(
                    montant_total=montant_total,
                    mode_paiement=random.choice(["especes", "wave", "orange_money"]),
                    utilisateur=random.choice([utilisateur_admin, utilisateur_caissier]),
                    client=random.choice(clients + [None, None]),
                )
                vente = VenteProduits.objects.create(transaction=transaction)
                for p, qte in lignes:
                    LigneVente.objects.create(vente=vente, produit=p, quantite=qte, prix_unitaire_vente=p.prix_vente)

            self.stdout.write("Réception de marchandise de démonstration...")
            appro = Approvisionnement.objects.create(fournisseur=fournisseurs[0])
            for p in random.sample(produits_crees, k=3):
                quantite, prix_lot = 20, p.prix_achat_moyen
                stock_avant, prix_avant = p.quantite_stock, p.prix_achat_moyen
                nouveau_stock = stock_avant + quantite
                valeur_totale = (stock_avant * prix_avant) + (quantite * prix_lot)
                p.prix_achat_moyen = (valeur_totale / nouveau_stock).quantize(Decimal("0.01"))
                p.quantite_stock = nouveau_stock
                p.save(update_fields=["quantite_stock", "prix_achat_moyen"])
                LigneAppro.objects.create(appro=appro, produit=p, quantite_recue=quantite, prix_unitaire_achat=prix_lot)
        else:
            self.stdout.write("Des transactions existent déjà — ventes/réception de démo ignorées.")

        self.stdout.write(self.style.SUCCESS(
            "\nTerminé.\n"
            "  Admin    -> identifiant: admin1     mot de passe: admin1234\n"
            "  Caissier -> identifiant: caissier1  mot de passe: caissier1234"
        ))
