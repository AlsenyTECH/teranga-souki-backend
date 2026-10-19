# fichier: superette/tests.py
# Tests de la logique métier : ventes, retours, annulations, caisse,
# approvisionnements, rapports, comptes et sécurité.
#
# Lancement (base SQLite jetable, aucun MySQL requis) :
#   DATABASE_URL=sqlite:///db_test.sqlite3 SECRET_KEY=test DEBUG=True \
#       python manage.py test superette

from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur,
    SessionCaisse, TransactionCaisse, Approvisionnement, RemboursementCredit, Employe,
    AjustementStock, Depense, InstantaneDonneesTest, LigneVente,
)

D = Decimal


class BaseTest(TestCase):
    """Jeu de données minimal : un admin, un caissier, deux produits, un client."""

    def setUp(self):
        self.role_admin = Role.objects.create(libelle="admin")
        self.role_caissier = Role.objects.create(libelle="caissier")
        self.admin = self.creer_utilisateur("admin", "admin", "700000001")
        self.caissier = self.creer_utilisateur("caissier", "caissier", "700000002")
        self.categorie = Categorie.objects.create(libelle="Épicerie")
        self.riz = Produit.objects.create(
            nom="Riz", prix_vente=D("1000"), prix_achat_moyen=D("600"), quantite_stock=D("50"),
            categorie=self.categorie, prix_vente_gros=D("900"), seuil_gros=D("10"),
        )
        self.huile = Produit.objects.create(
            nom="Huile", prix_vente=D("500"), prix_achat_moyen=D("300"), quantite_stock=D("50"),
            categorie=self.categorie,
        )
        self.client_awa = Client.objects.create(nom="Awa", telephone="770000100")
        self.api_admin = self.client_pour(self.admin)
        self.api_caissier = self.client_pour(self.caissier)

    def creer_utilisateur(self, username, role, telephone, **kwargs):
        user = User.objects.create_user(username, password="MotDePasse-Solide-42")
        return Utilisateur.objects.create(
            compte=user, nom=username.title(), telephone=telephone,
            role=self.role_admin if role == "admin" else self.role_caissier, **kwargs,
        )

    def client_pour(self, utilisateur):
        api = APIClient()
        api.force_authenticate(utilisateur.compte)
        return api

    def ouvrir_session(self, api, fond="0"):
        r = api.post("/api/sessions-caisse/ouvrir/", {"fond_ouverture": fond}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data["id"]

    def vendre(self, api, lignes, attendu=201, **extra):
        r = api.post("/api/ventes/", {"mode_paiement": "especes", "lignes": lignes, **extra}, format="json")
        self.assertEqual(r.status_code, attendu, getattr(r, "data", r))
        return r.data

    def recharger(self, *objets):
        for o in objets:
            o.refresh_from_db()


# ============================================================
# Prix et réductions : le serveur est la seule source de vérité
# ============================================================

class PrixEtRemisesTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.ouvrir_session(self.api_caissier)
        self.ouvrir_session(self.api_admin)

    def test_caissier_ne_peut_pas_changer_le_prix(self):
        r = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2, "prix_unitaire_vente": "1"}], attendu=400)
        self.assertIn("autorisation de modifier le prix", str(r))
        self.recharger(self.riz)
        self.assertEqual(self.riz.quantite_stock, D("50"))

    def test_caissier_ne_peut_pas_accorder_de_reduction(self):
        r = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 1}], attendu=400, reduction_montant="100")
        self.assertIn("réduction", str(r))

    def test_prix_catalogue_accepte_et_applique_si_absent(self):
        v = self.vendre(self.api_caissier, [
            {"produit": self.riz.id, "quantite": 2, "prix_unitaire_vente": "1000.00"},
            {"produit": self.huile.id, "quantite": 1},
        ])
        self.assertEqual(D(v["montant_total"]), D("2500.00"))

    def test_prix_de_gros_applique_automatiquement(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 10}])
        self.assertEqual(D(v["montant_total"]), D("9000.00"))
        # ...et le caissier peut envoyer ce prix de gros explicitement
        self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 12, "prix_unitaire_vente": "900"}])

    def test_caissier_avec_permission_remises(self):
        self.caissier.permissions_supplementaires = ["remises"]
        self.caissier.save()
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2, "prix_unitaire_vente": "800"}],
                        reduction_montant="100")
        self.assertEqual(D(v["montant_total"]), D("1500.00"))

    def test_admin_peut_negocier(self):
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 1, "prix_unitaire_vente": "750"}])
        self.assertEqual(D(v["montant_total"]), D("750.00"))

    def test_reduction_avec_trop_de_decimales_refusee_proprement(self):
        r = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 1}], attendu=400, reduction_montant=41.625)
        self.assertIn("reduction_montant", r)

    def test_stock_insuffisant(self):
        self.vendre(self.api_caissier, [{"produit": self.huile.id, "quantite": 51}], attendu=400)

    def test_vente_sans_session_refusee(self):
        autre = self.creer_utilisateur("c2", "caissier", "700000009")
        self.vendre(self.client_pour(autre), [{"produit": self.huile.id, "quantite": 1}], attendu=400)

    def test_permission_remises_acceptee_par_l_api_utilisateurs(self):
        r = self.api_admin.patch(f"/api/utilisateurs/{self.caissier.id}/permissions/",
                                 {"permissions_supplementaires": ["remises"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)


# ============================================================
# Annulation : tout est défait (stock, crédit, points, caisse)
# ============================================================

class AnnulationTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.session_caissier = self.ouvrir_session(self.api_caissier)
        self.session_admin = self.ouvrir_session(self.api_admin)

    def test_annulation_vente_credit_retire_dette_et_points(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}],
                        mode_paiement="credit", client=self.client_awa.id)
        self.recharger(self.client_awa)
        self.assertEqual(self.client_awa.solde_credit, D("2000"))
        self.assertEqual(self.client_awa.points_fidelite, 4)

        r = self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.assertEqual(r.status_code, 200, r.data)
        self.recharger(self.client_awa, self.riz)
        self.assertEqual(self.client_awa.solde_credit, D("0"))
        self.assertEqual(self.client_awa.points_fidelite, 0)
        self.assertEqual(self.riz.quantite_stock, D("50"))

    def test_points_jamais_negatifs(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}], client=self.client_awa.id)
        Client.objects.filter(pk=self.client_awa.pk).update(points_fidelite=1)  # points déjà utilisés
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.recharger(self.client_awa)
        self.assertEqual(self.client_awa.points_fidelite, 0)

    def test_retour_puis_annulation_ne_double_pas_le_stock(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}])
        ligne = v["lignes"][0]["id"]
        r = self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                                {"lignes": [{"ligne_vente": ligne, "quantite": 1}]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.recharger(self.riz)
        self.assertEqual(self.riz.quantite_stock, D("50"))

    def test_retour_partiel_credit_puis_annulation(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}],
                        mode_paiement="credit", client=self.client_awa.id)
        ligne = v["lignes"][0]["id"]
        self.api_admin.post(f"/api/ventes/{v['id']}/retour/", {"lignes": [{"ligne_vente": ligne, "quantite": 1}]}, format="json")
        self.recharger(self.client_awa)
        self.assertEqual(self.client_awa.solde_credit, D("1000"))
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.recharger(self.client_awa)
        self.assertEqual(self.client_awa.solde_credit, D("0"))

    def test_annulation_mixte_retire_la_part_credit(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 3}], mode_paiement="mixte",
                        client=self.client_awa.id,
                        paiements=[{"mode_paiement": "especes", "montant": "1000"},
                                   {"mode_paiement": "credit", "montant": "2000"}])
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.recharger(self.client_awa)
        self.assertEqual(self.client_awa.solde_credit, D("0"))

    def test_double_annulation_refusee(self):
        v = self.vendre(self.api_caissier, [{"produit": self.huile.id, "quantite": 1}])
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        r = self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.assertEqual(r.status_code, 400)

    def test_annulation_especes_session_fermee_sort_de_la_caisse_du_jour(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}])
        # Le caissier ferme sa caisse : 2000 F comptés dans son rapport Z.
        r = self.api_caissier.post(f"/api/sessions-caisse/{self.session_caissier}/fermer/",
                                   {"montant_compte": "2000"}, format="json")
        self.assertEqual(D(r.data["montant_attendu"]), D("2000"))
        self.assertEqual(D(r.data["ecart"]), D("0"))
        # L'admin annule le lendemain : il rend 2000 F depuis SA caisse.
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        courante = self.api_admin.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(courante["annulations_especes"]), D("2000"))
        self.assertEqual(D(courante["montant_attendu"]), D("-2000"))
        # Le rapport Z déjà figé de la session d'origine ne bouge pas.
        self.assertEqual(SessionCaisse.objects.get(pk=self.session_caissier).montant_attendu, D("2000"))

    def test_annulation_sans_session_passe_par_la_session_d_origine(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}])
        ligne = v["lignes"][0]["id"]
        self.api_admin.post(f"/api/ventes/{v['id']}/retour/", {"lignes": [{"ligne_vente": ligne, "quantite": 1}]}, format="json")
        # admin sans session : l'annulation passe par la session d'origine, encore ouverte
        self.api_admin.post(f"/api/sessions-caisse/{self.session_admin}/fermer/", {"montant_compte": "0"}, format="json")
        r = self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data["montant_annulation_especes"]), D("1000"))
        caisse = self.api_caissier.get("/api/sessions-caisse/courante/").data
        # +2000 vente, -1000 annulation ; les 1000 du retour sont sortis de la caisse admin.
        self.assertEqual(D(caisse["montant_attendu"]), D("1000"))

    def test_annulation_especes_sans_aucune_session_ouverte_refusee(self):
        v = self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 1}])
        self.api_caissier.post(f"/api/sessions-caisse/{self.session_caissier}/fermer/", {"montant_compte": "1000"}, format="json")
        self.api_admin.post(f"/api/sessions-caisse/{self.session_admin}/fermer/", {"montant_compte": "0"}, format="json")
        r = self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(TransactionCaisse.objects.get(pk=v["id"]).annulee)

    def test_caissier_ne_peut_pas_annuler(self):
        v = self.vendre(self.api_caissier, [{"produit": self.huile.id, "quantite": 1}])
        self.assertEqual(self.api_caissier.post(f"/api/ventes/{v['id']}/annuler/").status_code, 403)


# ============================================================
# Retours : jamais plus que ce qui a été payé
# ============================================================

class RetourTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.ouvrir_session(self.api_admin)

    def test_retour_total_apres_reduction_rend_le_montant_paye(self):
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}], reduction_montant="1000")
        self.assertEqual(D(v["montant_total"]), D("1000.00"))
        r = self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                                {"lignes": [{"ligne_vente": v["lignes"][0]["id"], "quantite": 2}]}, format="json")
        self.assertEqual(D(r.data["montant_retourne"]), D("1000.00"))
        self.assertEqual(D(r.data["montant_net"]), D("0.00"))

    def test_retours_partiels_successifs_somment_au_montant_paye(self):
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 3}], reduction_montant="100")
        ligne = v["lignes"][0]["id"]
        for _ in range(3):
            r = self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                                    {"lignes": [{"ligne_vente": ligne, "quantite": 1}]}, format="json")
            self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(D(r.data["montant_retourne"]), D("2900.00"))
        self.assertEqual(D(r.data["montant_net"]), D("0.00"))
        r = self.api_admin.post(f"/api/ventes/{v['id']}/retour/", {"lignes": [{"ligne_vente": ligne, "quantite": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_ligne_en_double_refusee(self):
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}])
        ligne = v["lignes"][0]["id"]
        r = self.api_admin.post(f"/api/ventes/{v['id']}/retour/", {"lignes": [
            {"ligne_vente": ligne, "quantite": 2}, {"ligne_vente": ligne, "quantite": 2}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.recharger(self.riz)
        self.assertEqual(self.riz.quantite_stock, D("48"))

    def test_retour_especes_compte_dans_le_rapport_z(self):
        v = self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 2}])
        self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                            {"lignes": [{"ligne_vente": v["lignes"][0]["id"], "quantite": 1}]}, format="json")
        caisse = self.api_admin.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(caisse["montant_attendu"]), D("500"))


# ============================================================
# Caisse : remboursements de crédit, ouverture
# ============================================================

class CaisseTests(BaseTest):
    def test_remboursement_credit_especes_compte_dans_la_caisse(self):
        Client.objects.filter(pk=self.client_awa.pk).update(solde_credit=D("3000"))
        self.ouvrir_session(self.api_admin, fond="1000")
        r = self.api_admin.post("/api/remboursements-credit/", {"client": self.client_awa.id, "montant": "2000"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        caisse = self.api_admin.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(caisse["remboursements_credit_especes"]), D("2000"))
        self.assertEqual(D(caisse["montant_attendu"]), D("3000"))

    def test_remboursement_wave_sans_session_accepte_et_hors_caisse(self):
        Client.objects.filter(pk=self.client_awa.pk).update(solde_credit=D("3000"))
        r = self.api_admin.post("/api/remboursements-credit/",
                                {"client": self.client_awa.id, "montant": "1000", "mode_paiement": "wave"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(RemboursementCredit.objects.get().session_caisse)

    def test_remboursement_especes_sans_session_refuse(self):
        Client.objects.filter(pk=self.client_awa.pk).update(solde_credit=D("3000"))
        r = self.api_admin.post("/api/remboursements-credit/", {"client": self.client_awa.id, "montant": "1000"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_remboursement_superieur_a_la_dette_refuse(self):
        Client.objects.filter(pk=self.client_awa.pk).update(solde_credit=D("500"))
        self.ouvrir_session(self.api_admin)
        r = self.api_admin.post("/api/remboursements-credit/", {"client": self.client_awa.id, "montant": "600"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_double_ouverture_refusee(self):
        self.ouvrir_session(self.api_caissier)
        r = self.api_caissier.post("/api/sessions-caisse/ouvrir/", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_vente_mixte_especes_comptee(self):
        self.ouvrir_session(self.api_caissier)
        self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}], mode_paiement="mixte",
                    paiements=[{"mode_paiement": "especes", "montant": "1500"},
                               {"mode_paiement": "wave", "montant": "500"}])
        caisse = self.api_caissier.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(caisse["ventes_especes"]), D("1500"))


# ============================================================
# Approvisionnements
# ============================================================

class ApproTests(BaseTest):
    def recevoir(self, **extra):
        r = self.api_admin.post("/api/approvisionnements/", {
            "fournisseur": self.fournisseur.id,
            "lignes": [{"produit": self.huile.id, "quantite_recue": 50, "prix_unitaire_achat": "400"}],
            **extra,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def setUp(self):
        super().setUp()
        self.fournisseur = Fournisseur.objects.create(nom="Grossiste")

    def test_cump_et_dette(self):
        self.recevoir(mode_paiement="credit")
        self.recharger(self.huile, self.fournisseur)
        self.assertEqual(self.huile.quantite_stock, D("100"))
        self.assertEqual(self.huile.prix_achat_moyen, D("350.00"))
        self.assertEqual(self.fournisseur.solde_du, D("20000"))

    def test_annulation_reception_restaure_tout(self):
        a = self.recevoir(mode_paiement="tranche", montant_paye="5000")
        r = self.api_admin.delete(f"/api/approvisionnements/{a['id']}/")
        self.assertEqual(r.status_code, 200, r.data)
        self.recharger(self.huile, self.fournisseur)
        self.assertEqual(self.huile.quantite_stock, D("50"))
        self.assertEqual(self.huile.prix_achat_moyen, D("300.00"))
        self.assertEqual(self.fournisseur.solde_du, D("0"))

    def test_retour_fournisseur_ne_rend_pas_le_stock_negatif(self):
        a = self.recevoir()
        Produit.objects.filter(pk=self.huile.pk).update(quantite_stock=D("10"))  # vendu entre-temps
        r = self.api_admin.post(f"/api/approvisionnements/{a['id']}/retour/",
                                {"lignes": [{"ligne_appro": a["lignes"][0]["id"], "quantite": 20}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.recharger(self.huile)
        self.assertEqual(self.huile.quantite_stock, D("10"))

    def test_reception_non_modifiable_apres_retour_client(self):
        self.ouvrir_session(self.api_admin)
        v = self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 2}])
        a = self.recevoir()
        # Retour client APRÈS la réception : le stock a bougé depuis.
        self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                            {"lignes": [{"ligne_vente": v["lignes"][0]["id"], "quantite": 1}]}, format="json")
        r = self.api_admin.delete(f"/api/approvisionnements/{a['id']}/")
        self.assertEqual(r.status_code, 400)

    def test_archive_fournisseur_ne_compte_que_la_part_a_credit(self):
        self.recevoir()  # comptant 20000 : aucune dette
        a = self.recevoir(mode_paiement="tranche", montant_paye="15000")  # 5000 dus
        annulee = self.recevoir(mode_paiement="credit")
        self.api_admin.delete(f"/api/approvisionnements/{annulee['id']}/")
        self.api_admin.post("/api/paiements-fournisseur/", {"fournisseur": self.fournisseur.id, "montant": "5000"}, format="json")
        r = self.api_admin.post(f"/api/fournisseurs/{self.fournisseur.id}/archiver-creance/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(D(r.data["total_recu_a_credit"]), D("5000"))
        self.assertEqual(D(r.data["total_paye"]), D("5000"))
        self.assertTrue(a)

    def test_paiement_fournisseur_superieur_a_la_dette_refuse(self):
        r = self.api_admin.post("/api/paiements-fournisseur/", {"fournisseur": self.fournisseur.id, "montant": "1"}, format="json")
        self.assertEqual(r.status_code, 400)


# ============================================================
# Rapports
# ============================================================

class RapportTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.ouvrir_session(self.api_admin)
        self.jour = timezone.localdate().isoformat()

    def test_date_invalide_400(self):
        self.assertEqual(self.api_admin.get("/api/rapports/?date=2026-13-01").status_code, 400)
        self.assertEqual(self.api_admin.get("/api/rapports/periode/?debut=x&fin=2026-01-01").status_code, 400)

    def test_ca_et_marge_nets_des_retours_et_reductions(self):
        # 2 riz à 1000 (CUMP 600) avec 200 F de réduction -> encaissé 1800, marge 800-200 = 600
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}], reduction_montant="200")
        r = self.api_admin.get(f"/api/rapports/?date={self.jour}").data
        self.assertEqual(r["chiffre_affaires_ventes"], D("1800.00"))
        self.assertEqual(r["marge_brute_ventes"], D("600.00"))
        # Retour d'un riz : rend 900 (prorata réduction), coût 600 -> marge rendue 300
        self.api_admin.post(f"/api/ventes/{v['id']}/retour/",
                            {"lignes": [{"ligne_vente": v["lignes"][0]["id"], "quantite": 1}]}, format="json")
        r = self.api_admin.get(f"/api/rapports/?date={self.jour}").data
        self.assertEqual(r["chiffre_affaires_ventes"], D("900.00"))
        self.assertEqual(r["retours_ventes"], D("900.00"))
        self.assertEqual(r["marge_brute_ventes"], D("300.00"))

    def test_resultat_net_retire_cout_depenses_et_salaires(self):
        # 2 riz à 1000 (CUMP 600) -> marge 800 ; dépense 300 ; salaire 200
        self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}])
        self.api_admin.post("/api/depenses/", {"montant": "300", "categorie": "Transport",
                                               "date_depense": self.jour}, format="json")
        employe = Employe.objects.create(nom="Babacar", salaire_mensuel=D("60000"))
        r = self.api_admin.post("/api/paiements-salaire/", {
            "employe": employe.id, "mois": self.jour, "type_paiement": "avance", "montant": "200",
            "date_paiement": self.jour,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.api_admin.get(f"/api/rapports/?date={self.jour}").data
        self.assertEqual(r["chiffre_affaires_total"], D("2000.00"))
        self.assertEqual(r["total_salaires"], D("200.00"))
        self.assertEqual(r["resultat_net"], D("300.00"))  # 800 - 300 - 200

    def test_periode_et_journalier_coherents_et_mixte_ventile(self):
        self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}], mode_paiement="mixte",
                    paiements=[{"mode_paiement": "especes", "montant": "1200"},
                               {"mode_paiement": "wave", "montant": "800"}])
        p = self.api_admin.get(f"/api/rapports/periode/?debut={self.jour}&fin={self.jour}").data
        j = self.api_admin.get(f"/api/rapports/?date={self.jour}").data
        self.assertEqual(p["chiffre_affaires_total"], j["chiffre_affaires_total"])
        self.assertEqual(p["repartition_paiement"]["especes"], D("1200"))
        self.assertEqual(p["repartition_paiement"]["wave"], D("800"))
        self.assertNotIn("mixte", p["repartition_paiement"])

    def test_vente_annulee_exclue(self):
        v = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}])
        self.api_admin.post(f"/api/ventes/{v['id']}/annuler/")
        r = self.api_admin.get(f"/api/rapports/?date={self.jour}").data
        self.assertEqual(r["chiffre_affaires_total"], D("0.00"))

    def test_caissier_sans_permission_refuse(self):
        self.assertEqual(self.api_caissier.get("/api/rapports/").status_code, 403)


# ============================================================
# Listes : filtres, pagination, visibilité
# ============================================================

class ListesTests(BaseTest):
    def test_caissier_ne_voit_que_ses_ventes(self):
        self.ouvrir_session(self.api_admin)
        self.ouvrir_session(self.api_caissier)
        self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 1}])
        self.vendre(self.api_caissier, [{"produit": self.huile.id, "quantite": 1}])
        self.assertEqual(len(self.api_caissier.get("/api/ventes/").data), 1)
        self.assertEqual(len(self.api_admin.get("/api/ventes/").data), 2)

    def test_pagination_et_filtre_de_dates(self):
        self.ouvrir_session(self.api_admin)
        for _ in range(3):
            self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 1}])
        r = self.api_admin.get("/api/ventes/?page=1&page_size=2").data
        self.assertEqual(r["count"], 3)
        self.assertEqual(len(r["results"]), 2)
        jour = timezone.localdate().isoformat()
        self.assertEqual(len(self.api_admin.get(f"/api/ventes/?date_debut={jour}&date_fin={jour}").data), 3)
        self.assertEqual(len(self.api_admin.get("/api/ventes/?date_debut=2999-01-01").data), 0)
        self.assertEqual(self.api_admin.get("/api/ventes/?date_debut=pas-une-date").status_code, 400)

    def test_suppression_produit_utilise_400(self):
        self.ouvrir_session(self.api_admin)
        self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 1}])
        r = self.api_admin.delete(f"/api/produits/{self.huile.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Produit.objects.filter(pk=self.huile.pk).exists())


# ============================================================
# Comptes et sécurité
# ============================================================

class ComptesTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.patron = self.creer_utilisateur("patron", "admin", "700000003", est_compte_principal=True)

    def test_un_admin_peut_reinitialiser_le_compte_du_patron(self):
        Token.objects.create(user=self.patron.compte)
        r = self.api_admin.patch(f"/api/utilisateurs/{self.patron.id}/",
                                 {"username": "souki", "nouveau_mot_de_passe": "Souki-Nouveau-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.patron.compte.refresh_from_db()
        self.assertEqual(self.patron.compte.username, "souki")
        self.assertTrue(self.patron.compte.check_password("Souki-Nouveau-2026"))
        self.assertFalse(Token.objects.filter(user=self.patron.compte).exists())  # sessions coupées
        # connexion avec les nouveaux identifiants
        r = APIClient().post("/api/connexion/", {"username": "souki", "password": "Souki-Nouveau-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        # toujours insupprimable, et un caissier ne peut rien modifier
        self.assertEqual(self.api_admin.delete(f"/api/utilisateurs/{self.patron.id}/").status_code, 400)
        r = self.api_caissier.patch(f"/api/utilisateurs/{self.patron.id}/", {"nouveau_mot_de_passe": "Pirate-2026-xyz"},
                                    format="json")
        self.assertEqual(r.status_code, 403)

    def test_patron_change_son_propre_mot_de_passe(self):
        api = self.client_pour(self.patron)
        r = api.patch("/api/mon-profil/", {"nouveau_mot_de_passe": "Nouveau-Solide-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("token", r.data)

    def test_mot_de_passe_faible_refuse(self):
        for mdp in ["12345678", "password", "abc"]:
            r = self.api_admin.post("/api/utilisateurs/", {
                "username": "nouveau", "password": mdp, "nom": "N", "telephone": "700000050", "role": "caissier",
            }, format="json")
            self.assertEqual(r.status_code, 400, mdp)
        r = self.api_admin.patch("/api/mon-profil/", {"nouveau_mot_de_passe": "12345678"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_telephone_duplique_400(self):
        r = self.api_admin.post("/api/utilisateurs/", {
            "username": "nouveau", "password": "Tres-Solide-2026", "nom": "N",
            "telephone": self.caissier.telephone, "role": "caissier",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_changement_mot_de_passe_revoque_les_jetons(self):
        Token.objects.create(user=self.caissier.compte)
        r = self.api_admin.patch(f"/api/utilisateurs/{self.caissier.id}/", {"nouveau_mot_de_passe": "Tres-Solide-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(Token.objects.filter(user=self.caissier.compte).exists())

    def test_connexion_deconnexion(self):
        api = APIClient()
        r = api.post("/api/connexion/", {"username": "caissier", "password": "MotDePasse-Solide-42"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        api.credentials(HTTP_AUTHORIZATION="Token " + r.data["token"])
        self.assertEqual(api.get("/api/mon-profil/").status_code, 200)
        self.assertEqual(api.post("/api/deconnexion/").status_code, 204)
        self.assertEqual(api.get("/api/mon-profil/").status_code, 401)

    def test_compte_desactive_ne_peut_plus_se_connecter(self):
        self.caissier.actif = False
        self.caissier.save()
        r = APIClient().post("/api/connexion/", {"username": "caissier", "password": "MotDePasse-Solide-42"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_suppression_compte_avec_historique_desactive_et_coupe_la_session(self):
        Token.objects.create(user=self.caissier.compte)
        self.ouvrir_session(self.api_caissier)
        r = self.api_admin.delete(f"/api/utilisateurs/{self.caissier.id}/")
        self.assertEqual(r.data["action"], "desactive")
        self.assertFalse(Token.objects.filter(user=self.caissier.compte).exists())

    def test_patron_non_supprimable(self):
        self.assertEqual(self.api_admin.delete(f"/api/utilisateurs/{self.patron.id}/").status_code, 400)


class SeedDemoTests(TestCase):
    @override_settings(DEBUG=False)
    def test_refuse_en_production(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())


class ServicesSupprimesTests(BaseTest):
    def test_plus_de_services_ni_prestations(self):
        self.assertEqual(self.api_admin.get("/api/services/").status_code, 404)
        self.assertEqual(self.api_admin.post("/api/prestations/", {}, format="json").status_code, 404)


# ============================================================
# Péremption, inventaire, salaires, comptabilité, sauvegarde
# ============================================================

class PeremptionTests(BaseTest):
    def recevoir(self, date_peremption, quantite=10):
        r = self.api_admin.post("/api/approvisionnements/", {
            "fournisseur": self.fournisseur.id,
            "lignes": [{"produit": self.huile.id, "quantite_recue": quantite, "prix_unitaire_achat": "300",
                        "date_peremption": date_peremption}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def setUp(self):
        super().setUp()
        self.fournisseur = Fournisseur.objects.create(nom="Grossiste")

    def test_le_produit_garde_la_peremption_la_plus_proche(self):
        a = self.recevoir("2026-12-31")
        self.assertEqual(a["lignes"][0]["date_peremption"], "2026-12-31")
        self.recharger(self.huile)
        self.assertEqual(str(self.huile.date_peremption), "2026-12-31")
        self.recevoir("2027-03-01")  # lot plus lointain : la date la plus proche reste
        self.recharger(self.huile)
        self.assertEqual(str(self.huile.date_peremption), "2026-12-31")
        self.recevoir("2026-11-15")
        self.recharger(self.huile)
        self.assertEqual(str(self.huile.date_peremption), "2026-11-15")
        produit = self.api_admin.get(f"/api/produits/{self.huile.id}/").data
        self.assertEqual(produit["date_peremption"], "2026-11-15")

    def test_rayon_vide_prend_la_date_du_nouveau_lot(self):
        Produit.objects.filter(pk=self.huile.pk).update(quantite_stock=0, date_peremption="2026-01-01")
        self.recevoir("2027-05-01")
        self.recharger(self.huile)
        self.assertEqual(str(self.huile.date_peremption), "2027-05-01")

    def test_date_modifiable_a_la_main(self):
        r = self.api_admin.patch(f"/api/produits/{self.huile.id}/", {"date_peremption": "2026-10-30"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        r = self.api_admin.patch(f"/api/produits/{self.huile.id}/", {"date_peremption": None}, format="json")
        self.assertIsNone(r.data["date_peremption"])

    def test_motif_perime_accepte(self):
        r = self.api_admin.post("/api/ajustements-stock/", {"produit": self.huile.id, "delta": "-2", "motif": "perime"},
                                format="json")
        self.assertEqual(r.status_code, 201, r.data)


class InventaireTests(BaseTest):
    def test_inventaire_complet(self):
        r = self.api_admin.post("/api/inventaires/", {"libelle": "Fin de mois"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        inv = r.data["id"]
        # un seul inventaire en cours
        self.assertEqual(self.api_admin.post("/api/inventaires/", {}, format="json").status_code, 400)
        # riz : 50 en théorie, 47 comptés ; huile : juste
        self.api_admin.post(f"/api/inventaires/{inv}/lignes/", {"produit": self.riz.id, "quantite_comptee": "47"}, format="json")
        r = self.api_admin.post(f"/api/inventaires/{inv}/lignes/", {"produit": self.huile.id, "quantite_comptee": "50"}, format="json")
        self.assertEqual(r.data["nb_lignes"], 2)
        self.assertEqual(r.data["nb_ecarts"], 1)
        self.assertEqual(D(str(r.data["valeur_ecarts"])), D("-1800"))  # -3 x CUMP 600
        # quantité non entière refusée pour un produit à l'unité
        r = self.api_admin.post(f"/api/inventaires/{inv}/lignes/", {"produit": self.riz.id, "quantite_comptee": "1.5"}, format="json")
        self.assertEqual(r.status_code, 400)
        # une vente pendant l'inventaire : l'écart se calcule sur le stock au moment de la validation
        self.ouvrir_session(self.api_admin)
        self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 2}])
        r = self.api_admin.post(f"/api/inventaires/{inv}/valider/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["statut"], "valide")
        self.recharger(self.riz, self.huile)
        self.assertEqual(self.riz.quantite_stock, D("47"))
        self.assertEqual(self.huile.quantite_stock, D("50"))
        self.assertEqual(AjustementStock.objects.filter(inventaire_id=inv).count(), 2)
        # figé après validation
        self.assertEqual(self.api_admin.post(f"/api/inventaires/{inv}/valider/").status_code, 400)
        self.assertEqual(self.api_admin.delete(f"/api/inventaires/{inv}/").status_code, 400)
        self.assertEqual(self.api_admin.post(f"/api/inventaires/{inv}/lignes/",
                                             {"produit": self.riz.id, "quantite_comptee": "1"}, format="json").status_code, 400)

    def test_abandon_et_droits(self):
        self.assertEqual(self.api_caissier.post("/api/inventaires/", {}, format="json").status_code, 403)
        inv = self.api_admin.post("/api/inventaires/", {}, format="json").data["id"]
        self.assertEqual(self.api_admin.post(f"/api/inventaires/{inv}/valider/").status_code, 400)  # vide
        self.assertEqual(self.api_admin.delete(f"/api/inventaires/{inv}/").status_code, 204)
        self.recharger(self.riz)
        self.assertEqual(self.riz.quantite_stock, D("50"))


class SalaireTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.employe = Employe.objects.create(nom="Babacar", poste="Aide", salaire_mensuel=D("60000"))

    def payer(self, montant, type_paiement="salaire", mois="2026-10-01", attendu=201):
        r = self.api_admin.post("/api/paiements-salaire/", {
            "employe": self.employe.id, "mois": mois, "type_paiement": type_paiement, "montant": montant,
            "date_paiement": "2026-10-15", "mode_paiement": "wave",
        }, format="json")
        self.assertEqual(r.status_code, attendu, r.data)
        return r.data

    def test_avance_puis_solde_puis_plafond(self):
        p = self.payer("20000", "avance", mois="2026-10-12")
        self.assertEqual(p["mois"], "2026-10-01")  # ramené au 1er du mois
        self.payer("40000")
        self.payer("1", attendu=400)  # salaire du mois déjà entièrement versé
        self.payer("5000", "prime")  # une prime reste possible
        self.assertEqual(len(self.api_admin.get(f"/api/paiements-salaire/?employe={self.employe.id}&mois=2026-10").data), 3)

    def test_reserve_a_l_admin(self):
        self.assertEqual(self.api_caissier.get("/api/employes/").status_code, 403)
        self.assertEqual(self.api_caissier.get("/api/paiements-salaire/").status_code, 403)

    def test_fiche_de_paie_liee_a_un_compte(self):
        r = self.api_admin.post("/api/employes/", {"utilisateur": self.caissier.id, "salaire_mensuel": "75000",
                                                   "poste": "Caissier"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["nom"], self.caissier.nom)  # repris du compte
        self.assertEqual(r.data["telephone"], self.caissier.telephone)
        self.assertEqual(r.data["utilisateur_username"], "caissier")
        # un compte = une seule fiche de paie
        r2 = self.api_admin.post("/api/employes/", {"utilisateur": self.caissier.id, "salaire_mensuel": "1"},
                                 format="json")
        self.assertEqual(r2.status_code, 400)
        # la liste des utilisateurs affiche le salaire
        u = next(u for u in self.api_admin.get("/api/utilisateurs/").data if u["id"] == self.caissier.id)
        self.assertEqual(u["employe_id"], r.data["id"])
        self.assertEqual(D(str(u["salaire_mensuel"])), D("75000"))
        admin = next(u for u in self.api_admin.get("/api/utilisateurs/").data if u["id"] == self.admin.id)
        self.assertIsNone(admin["employe_id"])

    def test_employe_sans_compte_exige_un_nom(self):
        r = self.api_admin.post("/api/employes/", {"salaire_mensuel": "50000"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_employe_paye_non_supprimable(self):
        self.payer("1000", "avance")
        self.assertEqual(self.api_admin.delete(f"/api/employes/{self.employe.id}/").status_code, 400)


class ComptabiliteTests(BaseTest):
    def test_tableau_de_bord(self):
        jour = timezone.localdate()
        self.ouvrir_session(self.api_admin)
        self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 2}])  # 2000 espèces, marge 800
        self.vendre(self.api_admin, [{"produit": self.huile.id, "quantite": 2}], mode_paiement="mixte",
                    client=self.client_awa.id,
                    paiements=[{"mode_paiement": "wave", "montant": "600"}, {"mode_paiement": "credit", "montant": "400"}])
        self.api_admin.post("/api/depenses/", {"montant": "300", "categorie": "Transport",
                                               "date_depense": jour.isoformat()}, format="json")
        r = self.api_admin.get(f"/api/comptabilite/?annee={jour.year}")
        self.assertEqual(r.status_code, 200, r.data)
        mois = r.data["mois"][-1]
        self.assertEqual(mois["chiffre_affaires"], D("3000.00"))
        self.assertEqual(mois["marge_brute"], D("1200.00"))
        self.assertEqual(mois["cout_marchandises"], D("1800.00"))
        self.assertEqual(mois["resultat_net"], D("900.00"))
        t = r.data["tresorerie"]
        self.assertEqual(t["encaissements"]["especes"], D("2000.00"))
        self.assertEqual(t["encaissements"]["wave"], D("600.00"))
        self.assertEqual(t["ventes_a_credit"], D("400.00"))
        self.assertEqual(r.data["situation"]["creances_clients"], D("400.00"))
        self.assertEqual(r.data["depenses_par_categorie"][0]["categorie"], "Transport")
        self.assertEqual(self.api_caissier.get("/api/comptabilite/").status_code, 403)


class SauvegardeTests(BaseTest):
    def test_telechargement_admin(self):
        import gzip, json
        r = self.api_admin.get("/api/sauvegarde/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        donnees = json.loads(gzip.decompress(r.content))
        modeles = {o["model"] for o in donnees}
        self.assertIn("superette.produit", modeles)
        self.assertIn("auth.user", modeles)
        self.assertNotIn("authtoken.token", modeles)  # jamais les jetons de connexion
        self.assertEqual(self.api_caissier.get("/api/sauvegarde/").status_code, 403)

    def test_commande_garde_les_plus_recentes(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as dossier:
            for i in range(3):
                (pathlib.Path(dossier) / f"teranga-souki-2026010{i}-0000.json.gz").write_bytes(b"x")
            call_command("sauvegarder", "--dossier", dossier, "--garder", "2", stdout=StringIO())
            fichiers = sorted(p.name for p in pathlib.Path(dossier).iterdir())
            self.assertEqual(len(fichiers), 2)
            self.assertNotIn("teranga-souki-20260100-0000.json.gz", fichiers)


# ============================================================
# Données de test : peupler_test / nettoyer_test
# ============================================================

class DonneesDeTestTests(TestCase):
    def peupler(self, jours=3):
        call_command("peupler_test", "--oui", "--jours", str(jours), "--mot-de-passe", "Test-Teranga-2026",
                     stdout=StringIO())

    def test_peupler_puis_nettoyer(self):
        self.peupler()
        self.assertEqual(User.objects.filter(username__startswith="test_").count(), 3)
        self.assertTrue(Produit.objects.exists())
        self.assertGreater(TransactionCaisse.objects.count(), 20)
        # 3 caisses laissées ouvertes aujourd'hui, les autres fermées
        self.assertEqual(SessionCaisse.objects.filter(statut="ouverte").count(), 3)
        self.assertFalse(Produit.objects.filter(quantite_stock__lt=0).exists())
        self.assertFalse(Client.objects.filter(solde_credit__lt=0).exists())
        self.assertGreaterEqual(sum(p.stock_bas() for p in Produit.objects.all()), 3)
        # l'historique est étalé sur plusieurs jours
        jours = {t.date_heure.date() for t in TransactionCaisse.objects.all()}
        self.assertEqual(len(jours), 4)
        # un compte de test se connecte avec le mot de passe choisi
        r = APIClient().post("/api/connexion/", {"username": "test_caissier1", "password": "Test-Teranga-2026"},
                             format="json")
        self.assertEqual(r.status_code, 200, r.data)

        call_command("nettoyer_test", "--oui", stdout=StringIO())
        self.assertFalse(Produit.objects.exists())
        self.assertFalse(TransactionCaisse.objects.exists())
        self.assertFalse(User.objects.filter(username__startswith="test_").exists())

    def test_peupler_refuse_une_base_non_vide(self):
        Produit.objects.create(nom="Réel", prix_vente=D("100"), categorie=Categorie.objects.create(libelle="X"))
        with self.assertRaises(CommandError):
            self.peupler()

    def test_peupler_exige_confirmation(self):
        with self.assertRaises(CommandError):
            call_command("peupler_test", stdout=StringIO())

    def test_nettoyer_refuse_si_donnees_reelles(self):
        self.peupler(jours=1)
        patron = Utilisateur.objects.create(
            compte=User.objects.create_user("patron", password="Solide-2026-x"), nom="Patron",
            telephone="700000099", role=Role.objects.get(libelle="admin"),
        )
        SessionCaisse.objects.create(utilisateur=patron)  # une vraie activité
        with self.assertRaises(CommandError):
            call_command("nettoyer_test", "--oui", stdout=StringIO())
        self.assertTrue(Produit.objects.exists())
        # le compte réel survit toujours à un nettoyage forcé
        call_command("nettoyer_test", "--oui", "--force", stdout=StringIO())
        self.assertTrue(User.objects.filter(username="patron").exists())
        self.assertFalse(Produit.objects.exists())


class DonneesDeTestCatalogueExistantTests(TestCase):
    """peupler_test --catalogue-existant sur une base qui contient le vrai
    catalogue : nettoyer_test doit tout remettre exactement comme avant."""

    def setUp(self):
        categorie = Categorie.objects.create(libelle="Boissons")
        self.produits = [
            Produit.objects.create(nom=f"Réel {i}", prix_vente=D(500 + 100 * i), categorie=categorie,
                                   quantite_stock=D(10 * i), prix_achat_moyen=D(400 + 50 * i) if i % 2 else D("0"))
            for i in range(6)
        ]
        self.produits.append(Produit.objects.create(nom="Oignon", prix_vente=D("600"), unite_vente="kg",
                                                    categorie=categorie, quantite_stock=D("12.5")))
        self.client_reel = Client.objects.create(nom="Vraie cliente", telephone="771000001",
                                                 solde_credit=D("2500"), points_fidelite=4)
        self.fournisseur_reel = Fournisseur.objects.create(nom="Vrai grossiste", solde_du=D("10000"))
        self.patronne = Utilisateur.objects.create(
            compte=User.objects.create_user("souki", password="Solide-2026-x"), nom="Yaye Fatou Ndiaye",
            telephone="700000099", role=Role.objects.create(libelle="admin"),
        )
        self.avant = {p.pk: (p.quantite_stock, p.prix_achat_moyen) for p in Produit.objects.all()}

    def peupler(self, *extra):
        call_command("peupler_test", "--oui", "--jours", "3", "--mot-de-passe", "Test-Teranga-2026",
                     "--catalogue-existant", *extra, stdout=StringIO())

    def test_peupler_sur_catalogue_reel_puis_restaurer(self):
        self.peupler()
        # catalogue réel trop petit (7 < 15) : complété par le catalogue de test
        self.assertGreater(Produit.objects.count(), 20)
        self.assertGreater(TransactionCaisse.objects.count(), 20)
        self.assertTrue(LigneVente.objects.filter(produit__in=self.produits).exists())
        self.assertTrue(Client.objects.filter(nom="Awa Ndiaye (test)", telephone__isnull=True).exists())
        self.assertFalse(Produit.objects.filter(quantite_stock__lt=0).exists())

        # Pendant la période de test : une vente faite avec le vrai compte et
        # un nouveau produit ajouté au catalogue.
        api = APIClient()
        api.force_authenticate(self.patronne.compte)
        self.assertEqual(api.post("/api/sessions-caisse/ouvrir/", {"fond_ouverture": "0"}, format="json").status_code,
                         201)
        r = api.post("/api/ventes/", {"mode_paiement": "credit", "client": self.client_reel.id, "lignes": [
            {"produit": self.produits[3].id, "quantite": "1", "prix_unitaire_vente": "800"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        nouveau = Produit.objects.create(nom="Ajouté pendant les tests", prix_vente=D("100"),
                                         categorie=Categorie.objects.first(), quantite_stock=D("9"))

        call_command("nettoyer_test", "--oui", stdout=StringIO())

        # les produits de test ajoutés sont repartis, le vrai catalogue reste
        self.assertEqual(Produit.objects.count(), 8)
        for p in Produit.objects.exclude(pk=nouveau.pk):
            self.assertEqual((p.quantite_stock, p.prix_achat_moyen), self.avant[p.pk], p.nom)
        nouveau.refresh_from_db()
        self.assertEqual(nouveau.quantite_stock, D("0"))
        self.client_reel.refresh_from_db()
        self.assertEqual((self.client_reel.solde_credit, self.client_reel.points_fidelite), (D("2500"), 4))
        self.fournisseur_reel.refresh_from_db()
        self.assertEqual(self.fournisseur_reel.solde_du, D("10000"))
        self.assertEqual(list(Client.objects.all()), [self.client_reel])
        self.assertEqual(list(Fournisseur.objects.all()), [self.fournisseur_reel])
        for modele in (TransactionCaisse, SessionCaisse, Approvisionnement, AjustementStock, Depense):
            self.assertFalse(modele.objects.exists(), modele.__name__)
        self.assertFalse(User.objects.filter(username__startswith="test_").exists())
        self.assertTrue(User.objects.filter(username="souki").exists())
        self.assertFalse(InstantaneDonneesTest.objects.exists())
        # Et on peut recommencer un cycle de test.
        self.peupler()

    def test_refuse_un_second_peuplement(self):
        # Instantané laissé par un peuplement précédent, même sans comptes test_*.
        InstantaneDonneesTest.objects.create(donnees={})
        with self.assertRaises(CommandError):
            self.peupler()

    def test_sans_option_la_base_reelle_est_refusee(self):
        with self.assertRaises(CommandError):
            call_command("peupler_test", "--oui", stdout=StringIO())
        self.assertFalse(InstantaneDonneesTest.objects.exists())

    def test_grand_catalogue_reel_non_complete(self):
        categorie = Categorie.objects.first()
        for i in range(10):
            Produit.objects.create(nom=f"Autre {i}", prix_vente=D("250"), categorie=categorie)
        self.peupler()
        self.assertEqual(Produit.objects.count(), 17)

    def test_nettoyer_tout_remet_a_zero(self):
        self.peupler()
        call_command("nettoyer_test", "--oui", "--tout", stdout=StringIO())
        for modele in (Produit, Categorie, Client, Fournisseur, TransactionCaisse, InstantaneDonneesTest):
            self.assertFalse(modele.objects.exists(), modele.__name__)
        self.assertTrue(User.objects.filter(username="souki").exists())
        self.assertFalse(User.objects.filter(username__startswith="test_").exists())


# ============================================================
# Livraisons : vente livrée, frais, paiement à la livraison,
# traçabilité et rémunération des livreurs
# ============================================================

from .models import Livreur, Livraison


class LivraisonTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.moto = Livreur.objects.create(nom="Modou", telephone="770000021", type_livreur="externe",
                                           remuneration_par_course=D("1000"))
        self.interne = Livreur.objects.create(nom="Babacar", telephone="780000013", type_livreur="interne")

    def vendre_livree(self, mode="especes", frais="1000", a_charge="client", livreur=None, attendu=201):
        livraison = {"adresse": "Rue 10, Médina", "frais": frais, "frais_a_charge": a_charge}
        if livreur:
            livraison["livreur"] = livreur.id
        return self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 2}], attendu=attendu,
                           mode_paiement=mode, client=self.client_awa.id, livraison=livraison)

    def action(self, livraison_id, action, donnees=None, api=None, attendu=200):
        r = (api or self.api_caissier).post(f"/api/livraisons/{livraison_id}/{action}/", donnees or {}, format="json")
        self.assertEqual(r.status_code, attendu, r.data)
        return r.data

    def test_frais_client_ajoutes_au_total_et_courseur_remunere(self):
        self.ouvrir_session(self.api_caissier)
        vente = self.vendre_livree(livreur=self.moto)
        self.assertEqual(D(vente["montant_total"]), D("3000"))
        self.assertEqual(D(vente["sous_total"]), D("2000"))
        self.assertEqual(vente["livraison"]["statut"], "en_attente")
        livraison = Livraison.objects.get()
        self.assertEqual(livraison.remuneration_livreur, D("1000"))
        self.assertEqual(livraison.contact_telephone, "770000100")
        self.assertEqual(len(livraison.historique), 1)

    def test_frais_a_la_charge_de_la_boutique_hors_ticket(self):
        self.ouvrir_session(self.api_caissier)
        vente = self.vendre_livree(a_charge="boutique", livreur=self.interne)
        self.assertEqual(D(vente["montant_total"]), D("2000"))
        self.assertEqual(Livraison.objects.get().remuneration_livreur, D("0"))

    def test_paiement_a_la_livraison_exige_une_livraison(self):
        self.ouvrir_session(self.api_caissier)
        self.vendre(self.api_caissier, [{"produit": self.riz.id, "quantite": 1}], attendu=400, mode_paiement="livraison")

    def test_parcours_complet_paiement_a_la_livraison(self):
        self.ouvrir_session(self.api_caissier, fond="5000")
        vente = self.vendre_livree(mode="livraison")
        lid = vente["livraison"]["id"]
        caisse = self.api_caissier.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(caisse["montant_attendu"]), D("5000"))  # rien d'encaissé au départ

        self.action(lid, "depart", attendu=400)  # pas de livreur
        self.action(lid, "assigner", {"livreur": self.moto.id})
        self.action(lid, "encaisser", attendu=400)  # pas encore livrée
        self.action(lid, "depart")
        self.action(lid, "livree")
        data = self.action(lid, "encaisser", {"mode_paiement": "especes"})
        self.assertTrue(data["encaisse"])
        self.action(lid, "encaisser", attendu=400)  # pas deux fois
        self.assertEqual([h["action"] for h in data["historique"]][-1], "Argent remis en caisse")
        self.assertEqual(len(data["historique"]), 5)

        caisse = self.api_caissier.get("/api/sessions-caisse/courante/").data
        self.assertEqual(D(caisse["livraisons_especes"]), D("3000"))
        self.assertEqual(D(caisse["montant_attendu"]), D("8000"))

    def test_echec_exige_un_motif(self):
        self.ouvrir_session(self.api_caissier)
        lid = self.vendre_livree(livreur=self.moto)["livraison"]["id"]
        self.action(lid, "echec", attendu=400)
        data = self.action(lid, "echec", {"motif": "Client absent"})
        self.assertEqual(data["statut"], "echouee")
        self.action(lid, "livree", attendu=400)

    def test_solde_du_et_paiement_du_livreur(self):
        self.ouvrir_session(self.api_caissier)
        for _ in range(2):
            lid = self.vendre_livree(livreur=self.moto)["livraison"]["id"]
            self.action(lid, "livree")
        livreur = self.api_admin.get(f"/api/livreurs/{self.moto.id}/").data
        self.assertEqual(D(livreur["solde_du"]), D("2000"))
        payer = {"livreur": self.moto.id, "montant": "2500", "mode_paiement": "especes", "date_paiement": "2026-10-19"}
        self.assertEqual(self.api_admin.post("/api/paiements-livreurs/", payer, format="json").status_code, 400)
        payer["montant"] = "1500"
        self.assertEqual(self.api_admin.post("/api/paiements-livreurs/", payer, format="json").status_code, 201)
        self.assertEqual(D(self.api_admin.get(f"/api/livreurs/{self.moto.id}/").data["solde_du"]), D("500"))
        # Le caissier voit les livreurs mais ne les paie pas.
        self.assertEqual(self.api_caissier.get("/api/livreurs/").status_code, 200)
        self.assertEqual(self.api_caissier.post("/api/paiements-livreurs/", payer, format="json").status_code, 403)

    def test_rapport_compte_frais_et_cout_livreur(self):
        self.ouvrir_session(self.api_caissier)
        lid = self.vendre_livree(livreur=self.moto)["livraison"]["id"]
        self.action(lid, "livree")
        jour = timezone.localdate().isoformat()
        r = self.api_admin.get(f"/api/rapports/?date={jour}").data
        # Marge produits 2 x 400 = 800, + 1000 de frais, - 1000 pour le livreur.
        self.assertEqual(D(r["marge_brute_ventes"]), D("1800"))
        self.assertEqual(D(r["cout_livraisons"]), D("1000"))
        self.assertEqual(D(r["resultat_net"]), D("800"))

    def test_annulation_de_la_vente_annule_la_livraison(self):
        self.ouvrir_session(self.api_caissier)
        self.ouvrir_session(self.api_admin)
        vente = self.vendre_livree(mode="livraison", livreur=self.moto)
        r = self.api_admin.post(f"/api/ventes/{vente['id']}/annuler/", format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Livraison.objects.get().statut, "annulee")
        self.action(vente["livraison"]["id"], "depart", attendu=400)

    def test_prix_de_la_course_modifiable_par_livraison(self):
        self.ouvrir_session(self.api_caissier)
        # Saisi en caisse pour cette course, différent du tarif habituel (1000 F).
        vente = self.vendre_livree(livreur=self.moto)
        livraison = Livraison.objects.get(pk=vente["livraison"]["id"])
        self.assertEqual(livraison.remuneration_livreur, D("1000"))
        self.ouvrir_session(self.api_admin)
        vente2 = self.vendre(self.api_admin, [{"produit": self.riz.id, "quantite": 1}], client=self.client_awa.id,
                             livraison={"adresse": "Ouakam", "frais": "2000", "livreur": self.moto.id,
                                        "remuneration_livreur": "1500"})
        self.assertEqual(Livraison.objects.get(pk=vente2["livraison"]["id"]).remuneration_livreur, D("1500"))
        # Corrigé après coup par l'admin ; le caissier ne peut pas.
        url = f"/api/livraisons/{livraison.id}/"
        self.assertEqual(self.api_caissier.patch(url, {"remuneration_livreur": "0"}, format="json").status_code, 403)
        r = self.api_admin.patch(url, {"remuneration_livreur": "1200"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data["remuneration_livreur"]), D("1200"))
        self.assertIn("1200", r.data["historique"][-1]["detail"])

    def test_liste_filtree_par_statut(self):
        self.ouvrir_session(self.api_caissier)
        lid = self.vendre_livree(livreur=self.moto)["livraison"]["id"]
        self.vendre_livree()
        self.action(lid, "livree")
        r = self.api_caissier.get("/api/livraisons/?statut=en_attente,en_cours")
        self.assertEqual(r.status_code, 200)
        donnees = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(donnees), 1)
