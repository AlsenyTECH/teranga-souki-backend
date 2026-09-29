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
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur, Service,
    SessionCaisse, TransactionCaisse, Approvisionnement, RemboursementCredit,
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

    def test_autre_admin_ne_peut_pas_modifier_le_patron(self):
        r = self.api_admin.patch(f"/api/utilisateurs/{self.patron.id}/", {"nouveau_mot_de_passe": "Pirate-2026-xyz"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.patron.compte.refresh_from_db()
        self.assertTrue(self.patron.compte.check_password("MotDePasse-Solide-42"))

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


class PrestationTests(BaseTest):
    def test_prestation_et_annulation(self):
        self.ouvrir_session(self.api_caissier)
        self.ouvrir_session(self.api_admin)
        service = Service.objects.create(libelle="Déplumage", tarif=D("250"))
        r = self.api_caissier.post("/api/prestations/", {"service": service.id, "quantite": 4, "mode_paiement": "especes"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(D(r.data["montant_total"]), D("1000"))
        r2 = self.api_admin.post(f"/api/ventes/{r.data['id']}/annuler/")
        self.assertEqual(r2.status_code, 200, r2.data)
        caisse = self.api_caissier.get("/api/sessions-caisse/courante/").data
        # vente comptée dans la session caissier, annulation sortie de la session admin
        self.assertEqual(D(caisse["montant_attendu"]), D("1000"))
        self.assertEqual(len(self.api_caissier.get("/api/prestations/").data), 1)
        self.assertTrue(Approvisionnement.objects.count() == 0)
