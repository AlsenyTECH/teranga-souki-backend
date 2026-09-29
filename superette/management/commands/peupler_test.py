# fichier: superette/management/commands/peupler_test.py
#
# Remplit la base avec un jeu de données de TEST réaliste : comptes de test,
# catalogue de supérette, fournisseurs, clients, puis N jours d'activité
# (réceptions, ventes, crédits, retours, annulations, prestations, dépenses,
# sessions de caisse ouvertes et fermées).
#
# Chaque opération passe par les VRAIES vues de l'API (permissions, verrous,
# calcul du CUMP, dettes, rapport Z...) : les chiffres sont donc cohérents
# entre eux, exactement comme si des caissiers avaient utilisé l'app.
# Les dates sont ensuite reculées jour par jour pour produire un historique.
#
# Lancement (y compris en production, sur une base vide) :
#   python manage.py peupler_test --oui
#   python manage.py peupler_test --oui --jours 10 --graine 7
# Nettoyage complet ensuite : python manage.py nettoyer_test --oui

import random
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from superette import views
from superette.models import (
    AjustementStock, Approvisionnement, Categorie, Client, Fournisseur, PaiementFournisseur, Produit,
    RemboursementCredit, RetourAppro, RetourVente, Role, Service, SessionCaisse, TransactionCaisse, Utilisateur,
)

PREFIXE_COMPTE = "test_"
D = Decimal

CATALOGUE = {
    "Épicerie": [
        # nom, prix vente, prix achat, unité, (prix gros, seuil gros), code-barre
        ("Riz brisé parfumé 5 kg", 4500, 3900, "unite", (4300, 5), "6001001000011"),
        ("Riz brisé au détail", 450, 380, "kg", None, None),
        ("Huile Dinor 1 L", 1400, 1150, "unite", (1300, 12), "6001001000028"),
        ("Sucre en morceaux 1 kg", 800, 650, "unite", (750, 10), "6001001000035"),
        ("Sucre en poudre au détail", 700, 560, "kg", None, None),
        ("Lait en poudre Vitalait 400 g", 2200, 1850, "unite", None, "6001001000042"),
        ("Café Touba 250 g", 1000, 750, "unite", None, "6001001000059"),
        ("Thé vert Achoura", 300, 210, "unite", (270, 20), "6001001000066"),
        ("Pâtes Panzani 500 g", 600, 470, "unite", (560, 12), "6001001000073"),
        ("Concentré de tomate 400 g", 650, 520, "unite", None, "6001001000080"),
        ("Bouillon Jumbo (boîte)", 1200, 950, "unite", None, "6001001000097"),
        ("Pain", 150, 125, "unite", None, None),
    ],
    "Boissons": [
        ("Eau Kirène 1,5 L", 500, 360, "unite", (450, 12), "6001002000010"),
        ("Coca-Cola 33 cl", 500, 350, "unite", (450, 24), "6001002000027"),
        ("Jus Pressea bissap 1 L", 1500, 1100, "unite", None, "6001002000034"),
        ("Gazelle 33 cl", 600, 430, "unite", None, "6001002000041"),
    ],
    "Frais": [
        ("Oignons", 600, 420, "kg", (550, 10), None),
        ("Pommes de terre", 700, 500, "kg", None, None),
        ("Poulet entier", 3000, 2400, "unite", None, None),
        ("Blanc de poulet", 4500, 3600, "kg", None, None),
        ("Œufs (plateau de 30)", 3000, 2550, "unite", None, None),
    ],
    "Hygiène": [
        ("Savon Madar", 350, 250, "unite", (320, 24), "6001004000016"),
        ("Eau de Javel 1 L", 600, 420, "unite", None, "6001004000023"),
        ("Dentifrice Signal", 900, 680, "unite", None, "6001004000030"),
        ("Papier toilette (x4)", 1200, 900, "unite", None, "6001004000047"),
    ],
    "Entretien": [
        ("Lessive Omo 500 g", 1100, 850, "unite", None, "6001005000015"),
        ("Liquide vaisselle", 800, 590, "unite", None, "6001005000022"),
    ],
}

FOURNISSEURS = [
    ("Grossiste Sandaga", "77 100 20 30"),
    ("SEDIMA Volailles", "33 800 11 22"),
    ("Kirène Distribution", "33 859 00 00"),
    ("Marché Castor (frais)", "76 555 44 33"),
    ("Hygiène Plus SARL", "78 222 11 00"),
]

CLIENTS = [
    ("Awa Ndiaye", "771000001", "Parcelles Assainies U17"),
    ("Mamadou Diallo", "771000002", "Grand Yoff"),
    ("Fatou Sow", "771000003", "Ouakam"),
    ("Ibrahima Fall", "771000004", "Liberté 6"),
    ("Aminata Diop", "771000005", "Médina"),
    ("Cheikh Mbaye", "771000006", "Pikine"),
    ("Khady Sarr", "771000007", "HLM Grand Médine"),
    ("Ousmane Ba", "771000008", "Point E"),
    ("Mariama Cissé", "771000009", "Sacré-Cœur 3"),
    ("Moussa Gueye", "771000010", "Yoff"),
    ("Ndeye Faye", "771000011", "Mermoz"),
    ("Abdoulaye Kane", "771000012", "Guédiawaye"),
    ("Restaurant Chez Fatou", "771000013", "Rue 10, Médina"),
    ("Boutique Sokhna", "771000014", "Keur Massar"),
    ("Pape Seck", "771000015", "Fann"),
]

SERVICES = [("Déplumage poulet", 250), ("Découpe poulet", 200)]

DEPENSES_QUOTIDIENNES = [("Transport marchandises", 1500, 4000), ("Sachets et emballages", 500, 2000)]


def arrondi(valeur, pas="0.01"):
    return Decimal(valeur).quantize(Decimal(pas), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Crée un jeu de données de TEST réaliste (comptes test_*, catalogue, N jours d'activité)."

    def add_arguments(self, parser):
        parser.add_argument("--oui", action="store_true", help="Confirme la création des données de test.")
        parser.add_argument("--jours", type=int, default=30, help="Nombre de jours d'historique (défaut 30).")
        parser.add_argument("--graine", type=int, default=2026, help="Graine aléatoire (même graine = mêmes données).")
        parser.add_argument("--mot-de-passe", dest="mot_de_passe", default=None,
                            help="Mot de passe des comptes de test (sinon généré aléatoirement et affiché).")

    # ------------------------------------------------------------ outillage

    def appeler(self, vue, methode, utilisateur, donnees=None, attendu=(200, 201, 204), **kwargs):
        """Appelle une vue de l'API comme le ferait l'app, sans passer par le
        réseau ni les limites de débit, et vérifie le code de réponse."""
        chemin = "/api/peupler-test/"
        requete = getattr(self.fabrique, methode)(chemin, donnees or {}, format="json")
        force_authenticate(requete, user=utilisateur.compte)
        reponse = vue(requete, **kwargs)
        if reponse.status_code not in attendu:
            nom = getattr(vue, "cls", vue).__name__
            raise CommandError(f"{nom} {methode.upper()} -> {reponse.status_code} : {reponse.data}")
        return reponse

    def vues(self):
        sans_limite = {"throttle_classes": []}
        return {
            "vente": views.VenteView.as_view(**sans_limite),
            "prestation": views.PrestationView.as_view(**sans_limite),
            "appro": views.ApprovisionnementView.as_view(**sans_limite),
            "appro_detail": views.ApprovisionnementDetailView.as_view(**sans_limite),
            "retour_appro": views.RetourApproView.as_view(**sans_limite),
            "retour": views.RetourVenteView.as_view(**sans_limite),
            "annuler": views.AnnulerVenteView.as_view(**sans_limite),
            "ouvrir": views.SessionCaisseOuvrirView.as_view(**sans_limite),
            "fermer": views.SessionCaisseFermerView.as_view(**sans_limite),
            "courante": views.SessionCaisseCouranteView.as_view(**sans_limite),
            "remboursement": views.RemboursementCreditView.as_view(**sans_limite),
            "paiement_fournisseur": views.PaiementFournisseurView.as_view(**sans_limite),
            "ajustement": views.AjustementStockView.as_view(**sans_limite),
            "depense": views.DepenseViewSet.as_view({"post": "create"}, **sans_limite),
        }

    # ------------------------------------------------------------ données de référence

    def creer_comptes(self, mot_de_passe):
        role_admin, _ = Role.objects.get_or_create(libelle="admin")
        role_caissier, _ = Role.objects.get_or_create(libelle="caissier")
        comptes = [
            ("test_admin", "Admin Test", "780000001", role_admin, []),
            ("test_caissier1", "Moussa Caissier (test)", "780000002", role_caissier, []),
            ("test_caissier2", "Aïssatou Caissière (test)", "780000003", role_caissier, ["remises", "clients"]),
        ]
        resultat = {}
        for username, nom, telephone, role, permissions in comptes:
            user = User.objects.create_user(username, password=mot_de_passe)
            resultat[username] = Utilisateur.objects.create(
                compte=user, nom=nom, telephone=telephone, role=role, permissions_supplementaires=permissions,
            )
        return resultat

    def creer_catalogue(self):
        produits = []
        for libelle, lignes in CATALOGUE.items():
            categorie, _ = Categorie.objects.get_or_create(libelle=libelle)
            for nom, prix_vente, prix_achat, unite, gros, code in lignes:
                produit = Produit.objects.create(
                    nom=nom, prix_vente=D(prix_vente), unite_vente=unite, categorie=categorie,
                    code_barre=code, seuil_alerte=D("5") if unite == "unite" else D("3"),
                    prix_vente_gros=D(gros[0]) if gros else None, seuil_gros=D(gros[1]) if gros else None,
                )
                produit.prix_achat_reference = D(prix_achat)
                produits.append(produit)
        return produits

    # ------------------------------------------------------------ opérations métier

    def recevoir(self, admin, fournisseur, lignes, mode="comptant", montant_paye=None):
        donnees = {
            "fournisseur": fournisseur.id,
            "numero_facture": f"FAC-{self.rng.randint(1000, 9999)}",
            "mode_paiement": mode,
            "lignes": lignes,
        }
        if montant_paye is not None:
            donnees["montant_paye"] = str(montant_paye)
        return self.appeler(self.v["appro"], "post", admin, donnees).data

    def reapprovisionner(self, admin, produits, fournisseurs, initial=False):
        a_recevoir = [p for p in produits if initial or p.quantite_stock <= p.seuil_alerte * 3]
        if not a_recevoir:
            return
        self.rng.shuffle(a_recevoir)
        # Une réception par groupe de produits, avec des modalités variées.
        for i in range(0, len(a_recevoir), 7):
            groupe = a_recevoir[i:i + 7]
            lignes = []
            for p in groupe:
                quantite = self.rng.choice([40, 60, 80]) if p.unite_vente == "unite" else self.rng.choice([25, 40, 50])
                prix = arrondi(p.prix_achat_reference * D(self.rng.uniform(0.95, 1.05)), "1")
                if p.unite_vente == "unite" and quantite % 20 == 0 and self.rng.random() < 0.4:
                    lignes.append({"produit": p.id, "quantite_recue": quantite,
                                   "prix_lot": str(prix * 20), "quantite_par_lot": 20})
                else:
                    lignes.append({"produit": p.id, "quantite_recue": quantite, "prix_unitaire_achat": str(prix)})
            if initial:
                mode, paye = "comptant", None
            else:
                mode = self.rng.choice(["comptant", "comptant", "tranche", "credit"])
                paye = None
                if mode == "tranche":
                    total = sum(
                        (D(l.get("prix_unitaire_achat") or D(l["prix_lot"]) / D(l["quantite_par_lot"])))
                        * D(l["quantite_recue"]) for l in lignes
                    )
                    paye = arrondi(total * D("0.5"), "1")
            self.recevoir(admin, self.rng.choice(fournisseurs), lignes, mode, paye)
        for p in produits:
            p.refresh_from_db()

    def vendre(self, caissier, produits, clients, peut_remiser):
        disponibles = [p for p in produits if p.quantite_stock >= 1]
        if not disponibles:
            return None
        lignes, sous_total = [], D("0")
        for p in self.rng.sample(disponibles, k=min(len(disponibles), self.rng.randint(1, 4))):
            if p.unite_vente == "kg":
                quantite = arrondi(D(self.rng.choice([0.25, 0.5, 0.75, 1, 1.5, 2, 2.5])), "0.001")
            elif p.seuil_gros and self.rng.random() < 0.08:
                quantite = p.seuil_gros  # achat en gros : le prix de gros s'applique
            else:
                quantite = D(self.rng.choice([1, 1, 1, 2, 2, 3]))
            if quantite > p.quantite_stock:
                continue
            prix = p.prix_pour(quantite)
            sous_total += prix * quantite
            lignes.append({"produit": p.id, "quantite": str(quantite), "prix_unitaire_vente": str(prix)})
        if not lignes:
            return None

        donnees = {"lignes": lignes}
        reduction = D("0")
        if peut_remiser and self.rng.random() < 0.15:
            reduction = arrondi(sous_total * D("0.05"), "1")
            donnees["reduction_montant"] = str(reduction)
        total = sous_total - reduction

        tirage = self.rng.random()
        client = self.rng.choice(clients) if self.rng.random() < 0.35 else None
        if tirage < 0.55:
            mode = "especes"
        elif tirage < 0.72:
            mode = "wave"
        elif tirage < 0.82:
            mode = "orange_money"
        elif tirage < 0.93:
            mode = "credit"
            client = client or self.rng.choice(clients)
        else:
            mode = "mixte"
            part_especes = arrondi(total * D("0.6"), "1")
            second = self.rng.choice(["wave", "credit"])
            if second == "credit":
                client = client or self.rng.choice(clients)
            donnees["paiements"] = [
                {"mode_paiement": "especes", "montant": str(part_especes)},
                {"mode_paiement": second, "montant": str(arrondi(total - part_especes))},
            ]
        donnees["mode_paiement"] = mode
        if client is not None:
            donnees["client"] = client.id
        vente = self.appeler(self.v["vente"], "post", caissier, donnees).data
        for p in produits:
            if any(l["produit"] == p.id for l in lignes):
                p.refresh_from_db()
        return vente

    # ------------------------------------------------------------ journée type

    def journee(self, jour, comptes, produits, clients, fournisseurs, services, fermer=True):
        admin = comptes["test_admin"]
        caissiers = [comptes["test_caissier1"], comptes["test_caissier2"]]
        reperes = self.reperes()

        self.reapprovisionner(admin, produits, fournisseurs)
        self.appeler(self.v["ouvrir"], "post", admin, {"fond_ouverture": "0"})
        ventes_du_jour = []
        for caissier in caissiers:
            self.appeler(self.v["ouvrir"], "post", caissier, {"fond_ouverture": str(self.rng.choice([10000, 15000, 20000]))})
            for _ in range(self.rng.randint(6, 14)):
                vente = self.vendre(caissier, produits, clients,
                                    peut_remiser="remises" in caissier.permissions_supplementaires)
                if vente:
                    ventes_du_jour.append(vente)
            for _ in range(self.rng.randint(0, 3)):
                client = self.rng.choice(clients) if self.rng.random() < 0.5 else None
                donnees = {"service": self.rng.choice(services).id, "quantite": self.rng.randint(1, 6),
                           "mode_paiement": self.rng.choice(["especes", "especes", "wave"])}
                if client:
                    donnees["client"] = client.id
                self.appeler(self.v["prestation"], "post", caissier, donnees)

        # Retour client occasionnel, et annulation plus rare (par l'admin).
        if ventes_du_jour and self.rng.random() < 0.35:
            vente = self.rng.choice(ventes_du_jour)
            ligne = vente["lignes"][0]
            quantite = D(ligne["quantite"]) if D(ligne["quantite"]) <= 1 else D("1")
            self.appeler(self.v["retour"], "post", admin, {
                "lignes": [{"ligne_vente": ligne["id"], "quantite": str(quantite)}],
                "commentaire": self.rng.choice(["Produit abîmé", "Erreur de produit", "Date proche"]),
            }, pk=vente["id"])
        if len(ventes_du_jour) > 1 and self.rng.random() < 0.15:
            vente = self.rng.choice([v for v in ventes_du_jour if not RetourVente.objects.filter(vente_id=v["id"]).exists()]
                                    or ventes_du_jour)
            self.appeler(self.v["annuler"], "post", admin, pk=vente["id"])

        # Remboursements de crédit (espèces ou mobile money).
        for client in Client.objects.filter(id__in=[c.id for c in clients], solde_credit__gt=0):
            if self.rng.random() < 0.25:
                montant = min(client.solde_credit, arrondi(client.solde_credit * D(self.rng.choice([0.3, 0.5, 1])), "1"))
                if montant > 0:
                    self.appeler(self.v["remboursement"], "post", admin, {
                        "client": client.id, "montant": str(montant),
                        "mode_paiement": self.rng.choice(["especes", "especes", "wave"]),
                    })
        # Paiement d'une partie de la dette fournisseur.
        for fournisseur in Fournisseur.objects.filter(id__in=[f.id for f in fournisseurs], solde_du__gt=0):
            montant = arrondi(fournisseur.solde_du * D("0.5"), "1")
            if self.rng.random() < 0.2 and montant > 0:
                self.appeler(self.v["paiement_fournisseur"], "post", admin, {
                    "fournisseur": fournisseur.id, "montant": str(montant),
                })
        # Dépenses du jour (+ loyer et électricité en début de mois).
        for categorie, mini, maxi in DEPENSES_QUOTIDIENNES:
            if self.rng.random() < 0.6:
                self.depense(admin, jour, categorie, self.rng.randint(mini, maxi) // 50 * 50, "journaliere")
        if jour.day == 1:
            self.depense(admin, jour, "Loyer", 150000, "mensuelle")
        if jour.day == 5:
            self.depense(admin, jour, "Électricité Senelec", self.rng.randint(35000, 60000) // 100 * 100, "mensuelle")
        if jour.weekday() == 5:
            self.depense(admin, jour, "Salaire journalier aide", 5000, "hebdomadaire")
        # Cas plus rares, forcés à dates fixes pour être sûrs de les avoir.
        numero_jour = (jour - self.premier_jour).days
        if numero_jour == 4:
            self.retour_fournisseur(admin)
        if numero_jour == 9:
            self.reception_annulee(admin, produits, fournisseurs)
        # Ajustement de stock occasionnel (casse, perte...).
        candidats = [p for p in produits if p.quantite_stock >= 2]
        if candidats and (self.rng.random() < 0.2 or numero_jour in (2, 12, 20)):
            candidat = self.rng.choice(candidats)
            self.appeler(self.v["ajustement"], "post", admin, {
                "produit": candidat.id, "delta": "-1", "motif": self.rng.choice(["casse", "perte", "comptage"]),
                "commentaire": "Constaté à l'inventaire",
            })
            candidat.refresh_from_db()

        if fermer:
            for utilisateur in [admin, *caissiers]:
                session = self.appeler(self.v["courante"], "get", utilisateur).data
                ecart = D(self.rng.choice([0, 0, 0, 0, -500, 500, -1000]))
                compte = max(D("0"), D(session["montant_attendu"]) + ecart)
                self.appeler(self.v["fermer"], "post", utilisateur,
                             {"montant_compte": str(compte), "commentaire": "RAS" if not ecart else "Écart constaté"},
                             pk=session["id"])
        self.antidater(jour, reperes)

    def retour_fournisseur(self, admin):
        """Renvoie 2 unités de la dernière réception d'un produit à l'unité."""
        for appro in Approvisionnement.objects.filter(annulee=False).order_by("-pk"):
            for ligne in appro.lignes.select_related("produit"):
                if ligne.produit.unite_vente == "unite" and ligne.produit.quantite_stock >= 2:
                    self.appeler(self.v["retour_appro"], "post", admin, {
                        "lignes": [{"ligne_appro": ligne.id, "quantite": "2"}],
                        "commentaire": "Emballages abîmés à la livraison",
                    }, pk=appro.id)
                    return

    def reception_annulee(self, admin, produits, fournisseurs):
        """Réception saisie par erreur puis annulée aussitôt (stock, CUMP et
        dette fournisseur reviennent à leur état d'avant)."""
        produit = self.rng.choice([p for p in produits if p.unite_vente == "unite"])
        appro = self.recevoir(admin, self.rng.choice(fournisseurs), [
            {"produit": produit.id, "quantite_recue": 12, "prix_unitaire_achat": str(produit.prix_achat_reference)},
        ], mode="credit")
        self.appeler(self.v["appro_detail"], "delete", admin, pk=appro["id"])
        produit.refresh_from_db()

    def stocks_bas(self, admin, produits):
        """Amène 3 produits sous leur seuil d'alerte (inventaire), pour tester
        les alertes de stock bas du tableau de bord."""
        for produit in self.rng.sample([p for p in produits if p.quantite_stock > p.seuil_alerte], k=3):
            cible = max(D("0"), produit.seuil_alerte - 1)
            self.appeler(self.v["ajustement"], "post", admin, {
                "produit": produit.id, "delta": str(cible - produit.quantite_stock), "motif": "comptage",
                "commentaire": "Inventaire : stock réel inférieur au stock théorique",
            })
            produit.refresh_from_db()

    def depense(self, admin, jour, categorie, montant, recurrence):
        self.appeler(self.v["depense"], "post", admin, {
            "montant": str(montant), "categorie": categorie, "date_depense": jour.isoformat(),
            "type_recurrence": recurrence, "description": "Donnée de test",
        })

    # ------------------------------------------------------------ dates

    MODELES_DATES = [
        (Approvisionnement, "date_reception"),
        (SessionCaisse, "date_ouverture"),
        (TransactionCaisse, "date_heure"),
        (RetourVente, "date_heure"),
        (RetourAppro, "date_heure"),
        (AjustementStock, "date_heure"),
        (RemboursementCredit, "date_heure"),
        (PaiementFournisseur, "date_heure"),
    ]

    def reperes(self):
        return {modele: (modele.objects.order_by("-pk").values_list("pk", flat=True).first() or 0)
                for modele, _ in self.MODELES_DATES}

    def antidater(self, jour, reperes):
        """Recule à `jour` tout ce qui a été créé depuis `reperes`, en gardant
        l'ordre de création (8 h -> 21 h)."""
        if jour == timezone.localdate():
            return
        debut = timezone.make_aware(datetime.combine(jour, time(7, 30)))
        nouveaux = []
        for modele, champ in self.MODELES_DATES:
            for obj in modele.objects.filter(pk__gt=reperes[modele]):
                nouveaux.append((getattr(obj, champ), modele, champ, obj.pk))
        nouveaux.sort(key=lambda n: n[0])
        pas = timedelta(minutes=13 * 60 / max(len(nouveaux), 1))
        for i, (_, modele, champ, pk) in enumerate(nouveaux):
            modele.objects.filter(pk=pk).update(**{champ: debut + pas * i})
        fin = timezone.make_aware(datetime.combine(jour, time(21, 30)))
        SessionCaisse.objects.filter(pk__gt=reperes[SessionCaisse], date_fermeture__isnull=False).update(date_fermeture=fin)
        TransactionCaisse.objects.filter(date_annulation__gt=debut + timedelta(days=1)).update(date_annulation=fin)

    # ------------------------------------------------------------ point d'entrée

    def handle(self, *args, **options):
        if not options["oui"]:
            raise CommandError("Ajoute --oui pour confirmer la création des données de test.")
        if User.objects.filter(username__startswith=PREFIXE_COMPTE).exists():
            raise CommandError("Des comptes test_* existent déjà : lance d'abord « nettoyer_test --oui ».")
        if Produit.objects.exists() or TransactionCaisse.objects.exists():
            raise CommandError(
                "La base contient déjà des produits ou des ventes : peupler_test ne s'utilise que sur une base vide "
                "(pour ne jamais mélanger données réelles et données de test)."
            )
        jours = options["jours"]
        if not 1 <= jours <= 120:
            raise CommandError("--jours doit être entre 1 et 120.")

        self.rng = random.Random(options["graine"])
        self.fabrique = APIRequestFactory()
        self.v = self.vues()
        mot_de_passe = options["mot_de_passe"] or f"Ts-{secrets.token_urlsafe(9)}"

        with transaction.atomic():
            comptes = self.creer_comptes(mot_de_passe)
            produits = self.creer_catalogue()
            fournisseurs = [Fournisseur.objects.create(nom=n, contact=c) for n, c in FOURNISSEURS]
            clients = [Client.objects.create(nom=n, telephone=t, adresse=a) for n, t, a in CLIENTS]
            services = [Service.objects.create(libelle=l, tarif=D(t)) for l, t in SERVICES]

            aujourd_hui = timezone.localdate()
            premier_jour = aujourd_hui - timedelta(days=jours)
            self.premier_jour = premier_jour
            reperes = self.reperes()
            self.reapprovisionner(comptes["test_admin"], produits, fournisseurs, initial=True)
            self.antidater(premier_jour - timedelta(days=1), reperes)

            for i in range(jours):
                jour = premier_jour + timedelta(days=i)
                self.journee(jour, comptes, produits, clients, fournisseurs, services)
                self.stdout.write(f"  {jour.isoformat()} ok")
            # Aujourd'hui : caisses laissées OUVERTES, pour tester la fermeture et le rapport Z.
            self.journee(aujourd_hui, comptes, produits, clients, fournisseurs, services, fermer=False)
            self.stocks_bas(comptes["test_admin"], produits)

        self.stdout.write(self.style.SUCCESS(
            f"\nDonnées de test créées : {jours} jours d'historique + aujourd'hui (caisses ouvertes).\n"
            f"  {Produit.objects.count()} produits, {Client.objects.count()} clients, "
            f"{Fournisseur.objects.count()} fournisseurs, {TransactionCaisse.objects.count()} ventes/prestations, "
            f"{Approvisionnement.objects.count()} réceptions, {SessionCaisse.objects.count()} sessions de caisse.\n\n"
            f"Comptes de test (mot de passe commun : {mot_de_passe}) :\n"
            f"  test_admin      admin\n"
            f"  test_caissier1  caissier (sans permission supplémentaire)\n"
            f"  test_caissier2  caissier avec permissions « remises » et « clients »\n\n"
            f"Tout effacer ensuite : python manage.py nettoyer_test --oui"
        ))
