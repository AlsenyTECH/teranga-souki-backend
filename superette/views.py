# fichier: superette/views.py
# Fichier déjà existant (créé par startapp) — on REMPLACE son contenu

from rest_framework import viewsets, status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction

from .models import (
    Categorie, Produit, Client, Fournisseur, Service, TransactionCaisse, Approvisionnement,
    Depense, Role, Utilisateur, RemboursementCredit, PaiementFournisseur,
    SessionCaisse, StatutSession,
)
from .serializers import (
    CategorieSerializer, ProduitSerializer, ClientSerializer,
    FournisseurSerializer, ServiceSerializer,
    VenteCreationSerializer, TransactionCaisseLectureSerializer,
    ApprovisionnementCreationSerializer, ApprovisionnementLectureSerializer,
    ApprovisionnementModificationSerializer, verifier_appro_modifiable, inverser_effet_appro,
    PrestationCreationSerializer, PrestationLectureSerializer,
    DepenseSerializer, RoleSerializer, UtilisateurSerializer, UtilisateurCreationSerializer,
    AjustementStockCreationSerializer, AjustementStockLectureSerializer,
    RemboursementCreditSerializer, RemboursementCreditLectureSerializer,
    ModifierPermissionsSerializer, MonProfilSerializer, UtilisateurEditionSerializer,
    ArchiveCreanceClientSerializer, ArchiveCreanceFournisseurSerializer,
    PaiementFournisseurSerializer, PaiementFournisseurLectureSerializer,
    SessionCaisseOuvertureSerializer, SessionCaisseFermetureSerializer,
    SessionCaisseLectureSerializer, calculer_totaux_session,
    RetourVenteCreationSerializer, RetourVenteLectureSerializer,
)
from .permissions import (
    LectureAdminEcritureAdmin, EstCaissierOuAdmin, EstAdmin,
    PeutGererCatalogue, PeutGererFournisseurs, PeutGererClients,
    PeutGererApprovisionnement, PeutGererDepenses, PeutVoirRapports,
)


class CategorieViewSet(viewsets.ModelViewSet):
    # ModelViewSet donne automatiquement les 5 actions REST standard :
    # list (GET /categories/), retrieve (GET /categories/1/),
    # create (POST), update (PUT/PATCH), destroy (DELETE)
    queryset = Categorie.objects.all()
    serializer_class = CategorieSerializer
    permission_classes = [PeutGererCatalogue]


class ProduitViewSet(viewsets.ModelViewSet):
    # Par défaut, ne montre QUE les produits actifs (comportement
    # correct pour la caisse : jamais vendre un produit désactivé).
    # Le paramètre ?tous=1 lève ce filtre — utilisé par la page
    # Catalogue admin, qui doit pouvoir voir ET réactiver un produit
    # désactivé, pas juste le perdre de vue après désactivation.
    queryset = Produit.objects.all().select_related("categorie")
    # select_related("categorie") : évite le problème "N+1 requêtes" —
    # sans ça, Django ferait une requête SQL supplémentaire par produit
    # rien que pour aller chercher son nom de catégorie. Un seul JOIN
    # au lieu de N requêtes = directement lié à ton exigence "rapide".
    serializer_class = ProduitSerializer
    permission_classes = [PeutGererCatalogue]

    def get_queryset(self):
        qs = super().get_queryset()
        # CRITIQUE : ce filtre ne doit s'appliquer qu'à la LISTE, jamais
        # à la récupération d'un objet précis (retrieve/update/delete).
        # Avant ce correctif, réactiver un produit désactivé échouait
        # silencieusement (404) : get_queryset() sert aussi de base à
        # get_object() pour un PATCH /produits/<id>/, donc le produit
        # désactivé devenait introuvable pour SA PROPRE modification —
        # exactement le contraire de l'effet recherché.
        if self.action == "list" and self.request.query_params.get("tous") != "1":
            qs = qs.filter(actif=True)
        # Permet à Flutter de chercher un produit par code-barres :
        # GET /produits/?code_barre=1234567890
        code_barre = self.request.query_params.get("code_barre")
        if code_barre:
            qs = qs.filter(code_barre=code_barre)
        return qs


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [PeutGererClients]


class FournisseurViewSet(viewsets.ModelViewSet):
    queryset = Fournisseur.objects.all()
    serializer_class = FournisseurSerializer
    permission_classes = [PeutGererFournisseurs]


class ServiceViewSet(viewsets.ModelViewSet):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [LectureAdminEcritureAdmin]


class VenteView(APIView):
    """
    POST /api/ventes/  — crée une vente complète (voir la docstring
    de VenteCreationSerializer pour le format JSON attendu).

    APIView plutôt que ModelViewSet ici : une création de vente n'est
    pas un simple CRUD, c'est une opération métier avec plusieurs
    effets de bord (stock, montant calculé) — un ViewSet standard
    forcerait à tordre la logique pour rentrer dans le moule create().
    """
    permission_classes = [EstCaissierOuAdmin]

    def post(self, request):
        serializer = VenteCreationSerializer(
            data=request.data,
            context={"utilisateur": request.user.utilisateur},
        )
        serializer.is_valid(raise_exception=True)
        transaction_caisse = serializer.save()
        # On relit l'objet créé avec le serializer de LECTURE, pour
        # renvoyer une réponse complète et lisible à Flutter (avec les
        # lignes, les noms de produits, etc.) plutôt que juste un id.
        out = TransactionCaisseLectureSerializer(transaction_caisse)
        return Response(out.data, status=status.HTTP_201_CREATED)

    def get(self, request):
        # Bonus : lister les ventes du jour, utile pour l'écran caisse
        qs = TransactionCaisse.objects.filter(
            venteproduits__isnull=False
        ).select_related("utilisateur", "client").prefetch_related(
            "venteproduits__lignes__produit", "retours__lignes", "paiements",
        )
        # ?client=<id> : historique des ventes d'un client précis, pour
        # sa fiche détail (Flutter fusionne ce résultat avec ses
        # remboursements de crédit pour construire une timeline unique).
        client_id = request.query_params.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)
        serializer = TransactionCaisseLectureSerializer(qs, many=True)
        return Response(serializer.data)


class ApprovisionnementView(APIView):
    """
    POST /api/approvisionnements/ — enregistre une réception de
    marchandise et recalcule automatiquement le CUMP de chaque produit.

    permission_classes = [PeutGererApprovisionnement] : contrairement à la vente (caissier
    ET admin), seul l'admin peut enregistrer une réception. C'est lui
    qui négocie avec les fournisseurs et valide les prix d'achat —
    cohérent avec les rôles qu'on a définis au cadrage. Un caissier
    ayant reçu la permission "approvisionnement" peut aussi le faire.
    """
    permission_classes = [PeutGererApprovisionnement]

    def post(self, request):
        serializer = ApprovisionnementCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appro = serializer.save()
        out = ApprovisionnementLectureSerializer(appro)
        return Response(out.data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = Approvisionnement.objects.select_related("fournisseur").prefetch_related("lignes__produit")
        # ?fournisseur=<id> : historique des réceptions d'un fournisseur
        # précis, pour sa fiche détail.
        fournisseur_id = request.query_params.get("fournisseur")
        if fournisseur_id:
            qs = qs.filter(fournisseur_id=fournisseur_id)
        serializer = ApprovisionnementLectureSerializer(qs, many=True)
        return Response(serializer.data)


class ApprovisionnementDetailView(APIView):
    """
    PATCH/DELETE /api/approvisionnements/<pk>/ — corrige ou annule une
    réception saisie par erreur. Refuse (400) si l'un de ses produits a
    bougé depuis (voir serializers.verifier_appro_modifiable) : le CUMP
    et le stock actuels dépendraient alors de mouvements postérieurs
    qu'on ne peut plus démêler proprement.
    """
    permission_classes = [PeutGererApprovisionnement]

    def get_object(self, pk):
        return get_object_or_404(Approvisionnement, pk=pk)

    def patch(self, request, pk):
        appro = self.get_object(pk)
        serializer = ApprovisionnementModificationSerializer(appro, data=request.data)
        serializer.is_valid(raise_exception=True)
        appro = serializer.save()
        out = ApprovisionnementLectureSerializer(appro)
        return Response(out.data)

    def delete(self, request, pk):
        appro = self.get_object(pk)
        with transaction.atomic():
            appro = Approvisionnement.objects.select_for_update().get(pk=appro.pk)
            verifier_appro_modifiable(appro)
            inverser_effet_appro(appro)
            appro.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PrestationView(APIView):
    """
    POST /api/prestations/ — enregistre une prestation de service
    (déplumage de poulet, ou tout autre service futur du catalogue).
    """
    permission_classes = [EstCaissierOuAdmin]

    def post(self, request):
        serializer = PrestationCreationSerializer(
            data=request.data,
            context={"utilisateur": request.user.utilisateur},
        )
        serializer.is_valid(raise_exception=True)
        transaction_caisse = serializer.save()
        out = PrestationLectureSerializer(transaction_caisse)
        return Response(out.data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = TransactionCaisse.objects.filter(
            prestationservice__isnull=False
        ).select_related("utilisateur", "client", "prestationservice__service")
        serializer = PrestationLectureSerializer(qs, many=True)
        return Response(serializer.data)


class DepenseViewSet(viewsets.ModelViewSet):
    queryset = Depense.objects.select_related("utilisateur").all()
    serializer_class = DepenseSerializer
    permission_classes = [PeutGererDepenses]

    def perform_create(self, serializer):
        # perform_create() est l'endroit correct pour injecter un champ
        # non fourni par le client mais déduit du contexte de la requête
        # — ici, on force utilisateur = la personne connectée, peu
        # importe ce que contiendrait (ou pas) le JSON envoyé.
        serializer.save(utilisateur=self.request.user.utilisateur)


# ============================================================
# Chapitre 16 — Rapports : chiffre d'affaires, marge, dépenses,
# résultat net, produits les plus vendus, stock bas.
# ============================================================

from datetime import date as date_cls, timedelta
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, TruncDate
from django.db import transaction
from .models import LigneVente, Depense as DepenseModel, AjustementStock, ModePaiement


def evolution_pct(actuel, ancien):
    """% d'évolution entre deux valeurs — None si pas de base de
    comparaison valable (période précédente à 0), plutôt qu'une fausse
    division. Partagé entre RapportJournalierView et RapportPeriodeView."""
    actuel, ancien = float(actuel), float(ancien)
    if ancien == 0:
        return None
    return round((actuel - ancien) / ancien * 100, 1)


class RapportJournalierView(APIView):
    """
    GET /api/rapports/?date=2026-08-16   (date optionnelle, défaut = aujourd'hui)
    """
    permission_classes = [PeutVoirRapports]

    def _totaux_jour(self, jour):
        transactions_du_jour = TransactionCaisse.objects.filter(
            date_heure__date=jour, annulee=False
        )

        # Coalesce(Sum(...), 0) : Sum() renvoie None si aucune ligne ne
        # correspond (ex: aucune vente ce jour-là) — Coalesce remplace
        # ce None par 0, pour éviter un calcul cassé plus bas (None - 500).
        ca_ventes = transactions_du_jour.filter(venteproduits__isnull=False).aggregate(
            total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField())
        )["total"]

        ca_prestations = transactions_du_jour.filter(prestationservice__isnull=False).aggregate(
            total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField())
        )["total"]

        total_depenses = DepenseModel.objects.filter(date_depense=jour).aggregate(
            total=Coalesce(Sum("montant"), 0, output_field=DecimalField())
        )["total"]

        # Marge sur les ventes de produits : (prix de vente appliqué -
        # prix d'achat moyen ACTUEL du produit) x quantité. Approximation
        # assumée : on utilise le CUMP d'AUJOURD'HUI, pas celui du jour
        # de la vente (qu'on ne conserve pas en historique) — cohérent
        # avec le compromis "simple" qu'on avait choisi au chapitre 4.
        marge_expr = ExpressionWrapper(
            (F("prix_unitaire_vente") - F("produit__prix_achat_moyen")) * F("quantite"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        marge_ventes = LigneVente.objects.filter(
            vente__transaction__date_heure__date=jour,
            vente__transaction__annulee=False,
        ).aggregate(total=Coalesce(Sum(marge_expr), 0, output_field=DecimalField()))["total"]

        chiffre_affaires = ca_ventes + ca_prestations
        resultat_net = chiffre_affaires - total_depenses

        return {
            "chiffre_affaires_ventes": ca_ventes,
            "chiffre_affaires_prestations": ca_prestations,
            "chiffre_affaires_total": chiffre_affaires,
            "marge_brute_ventes": marge_ventes,
            "total_depenses": total_depenses,
            "resultat_net": resultat_net,
        }

    def get(self, request):
        date_param = request.query_params.get("date")
        jour = date_cls.fromisoformat(date_param) if date_param else timezone.localdate()

        totaux = self._totaux_jour(jour)
        totaux_veille = self._totaux_jour(jour - timedelta(days=1))

        # Produits les plus vendus du jour (top 5)
        top_produits = (
            LigneVente.objects.filter(
                vente__transaction__date_heure__date=jour,
                vente__transaction__annulee=False,
            )
            .values("produit__nom")
            .annotate(quantite_totale=Sum("quantite"))
            .order_by("-quantite_totale")[:5]
        )

        # Produits en stock bas, tous les jours (pas propre au "jour" demandé)
        produits_stock_bas = Produit.objects.filter(
            actif=True, quantite_stock__lte=F("seuil_alerte")
        ).values("id", "nom", "quantite_stock", "seuil_alerte")

        return Response({
            "date": jour.isoformat(),
            **totaux,
            "top_produits": list(top_produits),
            "produits_stock_bas": list(produits_stock_bas),
            # Comparaison avec la veille — donne un sens réel aux mini-
            # indicateurs de variation affichés sur chaque carte KPI du
            # tableau de bord (aucune valeur inventée : soit un vrai %,
            # soit None si la veille n'a aucune base de comparaison).
            "comparaison_veille": {
                "evolution_chiffre_affaires_pct": evolution_pct(
                    totaux["chiffre_affaires_total"], totaux_veille["chiffre_affaires_total"]),
                "evolution_marge_brute_pct": evolution_pct(
                    totaux["marge_brute_ventes"], totaux_veille["marge_brute_ventes"]),
                "evolution_depenses_pct": evolution_pct(
                    totaux["total_depenses"], totaux_veille["total_depenses"]),
                "evolution_resultat_net_pct": evolution_pct(
                    totaux["resultat_net"], totaux_veille["resultat_net"]),
            },
        })


class RapportPeriodeView(APIView):
    """
    GET /api/rapports/periode/?debut=YYYY-MM-DD&fin=YYYY-MM-DD

    Rapport complet sur une période arbitraire (jour, semaine, mois,
    année ou intervalle personnalisé — c'est le frontend qui calcule
    les bornes selon le préréglage choisi et les envoie ici). Inclut
    une comparaison automatique avec la période précédente de même
    durée, pour donner un sens aux chiffres ("+12% vs période précédente").
    """
    permission_classes = [PeutVoirRapports]

    def get(self, request):
        debut_str = request.query_params.get("debut")
        fin_str = request.query_params.get("fin")
        if not debut_str or not fin_str:
            return Response(
                {"detail": "Paramètres 'debut' et 'fin' requis (format YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            debut = date_cls.fromisoformat(debut_str)
            fin = date_cls.fromisoformat(fin_str)
        except ValueError:
            return Response({"detail": "Format de date invalide."}, status=status.HTTP_400_BAD_REQUEST)
        if fin < debut:
            return Response({"detail": "La date de fin doit être après la date de début."}, status=status.HTTP_400_BAD_REQUEST)

        donnees = self._calculer(debut, fin)

        # Comparaison avec la période précédente de MÊME durée, juste
        # avant — c'est ce qui donne un sens à "87 350 F" : est-ce
        # mieux ou moins bien que d'habitude ?
        duree = (fin - debut).days + 1
        debut_precedent = debut - timedelta(days=duree)
        fin_precedent = debut - timedelta(days=1)
        precedent = self._calculer(debut_precedent, fin_precedent)

        donnees["comparaison"] = {
            "chiffre_affaires_precedent": precedent["chiffre_affaires_total"],
            "resultat_net_precedent": precedent["resultat_net"],
            "marge_brute_ventes_precedent": precedent["marge_brute_ventes"],
            "total_depenses_precedent": precedent["total_depenses"],
            "evolution_chiffre_affaires_pct": evolution_pct(
                donnees["chiffre_affaires_total"], precedent["chiffre_affaires_total"]),
            "evolution_resultat_net_pct": evolution_pct(
                donnees["resultat_net"], precedent["resultat_net"]),
            "evolution_marge_brute_pct": evolution_pct(
                donnees["marge_brute_ventes"], precedent["marge_brute_ventes"]),
            "evolution_depenses_pct": evolution_pct(
                donnees["total_depenses"], precedent["total_depenses"]),
        }

        # Snapshots indépendants de la période choisie — l'état ACTUEL
        # du business, toujours utile à côté de l'analyse historique.
        donnees["valeur_stock_totale"] = Produit.objects.filter(actif=True).aggregate(
            total=Coalesce(Sum(F("quantite_stock") * F("prix_achat_moyen")), 0, output_field=DecimalField())
        )["total"]
        donnees["creances_clients_totales"] = Client.objects.aggregate(
            total=Coalesce(Sum("solde_credit"), 0, output_field=DecimalField())
        )["total"]

        return Response(donnees)

    def _calculer(self, debut, fin):
        transactions = TransactionCaisse.objects.filter(
            date_heure__date__gte=debut, date_heure__date__lte=fin, annulee=False
        )
        ventes_qs = transactions.filter(venteproduits__isnull=False)
        prestations_qs = transactions.filter(prestationservice__isnull=False)

        ca_ventes = ventes_qs.aggregate(total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField()))["total"]
        ca_prestations = prestations_qs.aggregate(total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField()))["total"]
        nb_transactions = transactions.count()
        nb_ventes = ventes_qs.count()

        marge_expr = ExpressionWrapper(
            (F("prix_unitaire_vente") - F("produit__prix_achat_moyen")) * F("quantite"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        marge = LigneVente.objects.filter(
            vente__transaction__date_heure__date__gte=debut,
            vente__transaction__date_heure__date__lte=fin,
            vente__transaction__annulee=False,
        ).aggregate(total=Coalesce(Sum(marge_expr), 0, output_field=DecimalField()))["total"]

        total_depenses = DepenseModel.objects.filter(
            date_depense__gte=debut, date_depense__lte=fin
        ).aggregate(total=Coalesce(Sum("montant"), 0, output_field=DecimalField()))["total"]

        chiffre_affaires_total = ca_ventes + ca_prestations
        resultat_net = chiffre_affaires_total - total_depenses
        panier_moyen = round(float(ca_ventes) / nb_ventes, 2) if nb_ventes > 0 else 0

        # Répartition par mode de paiement — utile pour anticiper les
        # besoins de monnaie/liquidités et suivre l'adoption du mobile money.
        repartition_paiement = {
            mode: transactions.filter(mode_paiement=mode).aggregate(
                total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField())
            )["total"]
            for mode, _ in ModePaiement.choices
        }

        top_produits = list(
            LigneVente.objects.filter(
                vente__transaction__date_heure__date__gte=debut,
                vente__transaction__date_heure__date__lte=fin,
                vente__transaction__annulee=False,
            )
            .values("produit__nom")
            .annotate(quantite_totale=Sum("quantite"),
                      chiffre_affaires=Sum(F("quantite") * F("prix_unitaire_vente")))
            .order_by("-quantite_totale")[:10]
        )

        # Répartition du chiffre d'affaires par catégorie de produit —
        # matière première du graphique donut côté app (chaque Produit a
        # une catégorie obligatoire, donc aucune ligne ne tombe hors
        # regroupement ici, contrairement à depenses_par_categorie qui
        # est une simple chaîne libre).
        ventes_par_categorie = list(
            LigneVente.objects.filter(
                vente__transaction__date_heure__date__gte=debut,
                vente__transaction__date_heure__date__lte=fin,
                vente__transaction__annulee=False,
            )
            .values(categorie=F("produit__categorie__libelle"))
            .annotate(chiffre_affaires=Sum(F("quantite") * F("prix_unitaire_vente")))
            .order_by("-chiffre_affaires")
        )

        # Produits actifs n'ayant fait l'objet d'AUCUNE vente sur la
        # période — signal utile pour repérer le stock qui dort.
        produits_vendus_ids = LigneVente.objects.filter(
            vente__transaction__date_heure__date__gte=debut,
            vente__transaction__date_heure__date__lte=fin,
        ).values_list("produit_id", flat=True).distinct()
        produits_invendus = list(
            Produit.objects.filter(actif=True)
            .exclude(id__in=produits_vendus_ids)
            .values("id", "nom", "quantite_stock")[:15]
        )

        depenses_par_categorie = list(
            DepenseModel.objects.filter(date_depense__gte=debut, date_depense__lte=fin)
            .values("categorie")
            .annotate(total=Sum("montant"))
            .order_by("-total")
        )

        # Évolution jour par jour dans la période — matière première du
        # graphique d'évolution du chiffre d'affaires côté app.
        evolution_brute = (
            transactions.annotate(jour=TruncDate("date_heure"))
            .values("jour")
            .annotate(total=Sum("montant_total"))
            .order_by("jour")
        )

        return {
            "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
            "chiffre_affaires_ventes": ca_ventes,
            "chiffre_affaires_prestations": ca_prestations,
            "chiffre_affaires_total": chiffre_affaires_total,
            "nombre_transactions": nb_transactions,
            "panier_moyen": panier_moyen,
            "marge_brute_ventes": marge,
            "total_depenses": total_depenses,
            "resultat_net": resultat_net,
            "repartition_paiement": repartition_paiement,
            "top_produits": top_produits,
            "ventes_par_categorie": ventes_par_categorie,
            "produits_invendus": produits_invendus,
            "depenses_par_categorie": depenses_par_categorie,
            "evolution_quotidienne": [
                {"date": e["jour"].isoformat(), "total": e["total"]} for e in evolution_brute
            ],
        }


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    # Lecture seule : les rôles ("admin"/"caissier") sont fixes, créés
    # une fois en base — pas d'API pour en ajouter à la volée.
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [EstAdmin]


class UtilisateurListCreateView(APIView):
    """
    GET  /api/utilisateurs/  — liste les comptes (admin uniquement)
    POST /api/utilisateurs/  — crée un nouveau compte (compte Django + profil métier)
    """
    permission_classes = [EstAdmin]

    def get(self, request):
        qs = Utilisateur.objects.select_related("compte", "role").all()
        return Response(UtilisateurSerializer(qs, many=True).data)

    def post(self, request):
        serializer = UtilisateurCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur = serializer.save()
        return Response(UtilisateurSerializer(utilisateur).data, status=status.HTTP_201_CREATED)


class UtilisateurPermissionsView(APIView):
    """
    PATCH /api/utilisateurs/<id>/permissions/ — modifie UNIQUEMENT les
    permissions accordées à un compte existant (pas son mot de passe,
    pas son nom — ce sont des choses différentes, jamais mélangées
    dans le même endpoint).
    """
    permission_classes = [EstAdmin]

    def patch(self, request, pk):
        try:
            utilisateur = Utilisateur.objects.get(pk=pk)
        except Utilisateur.DoesNotExist:
            return Response({"detail": "Compte introuvable."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ModifierPermissionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        utilisateur.permissions_supplementaires = serializer.validated_data["permissions_supplementaires"]
        utilisateur.save(update_fields=["permissions_supplementaires"])
        return Response(UtilisateurSerializer(utilisateur).data)


class UtilisateurDetailView(APIView):
    """
    PATCH /api/utilisateurs/<id>/ — un admin modifie le nom, le
    téléphone, l'identifiant de connexion et/ou le mot de passe d'un
    AUTRE compte. Distinct de UtilisateurPermissionsView (permissions
    uniquement) et de MonProfilView (auto-édition) — jamais mélangés.
    """
    permission_classes = [EstAdmin]

    def patch(self, request, pk):
        try:
            utilisateur = Utilisateur.objects.select_related("compte").get(pk=pk)
        except Utilisateur.DoesNotExist:
            return Response({"detail": "Compte introuvable."}, status=status.HTTP_404_NOT_FOUND)

        serializer = UtilisateurEditionSerializer(instance=utilisateur, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "nom" in data:
            utilisateur.nom = data["nom"]
        if "telephone" in data:
            utilisateur.telephone = data["telephone"]
        utilisateur.save()

        if "username" in data:
            utilisateur.compte.username = data["username"]
        nouveau_mdp = data.get("nouveau_mot_de_passe")
        if nouveau_mdp:
            utilisateur.compte.set_password(nouveau_mdp)
        if "username" in data or nouveau_mdp:
            utilisateur.compte.save()

        return Response(UtilisateurSerializer(utilisateur).data)


class MonProfilView(APIView):
    """
    GET   /api/mon-profil/ — infos du compte connecté
    PATCH /api/mon-profil/ — auto-modification : un admin peut changer
    son nom/téléphone ET son mot de passe ; un caissier ne peut changer
    que son mot de passe (nom/téléphone ignorés silencieusement s'il
    les envoie quand même — pas d'erreur, juste pas d'effet).
    """
    permission_classes = [EstCaissierOuAdmin]

    def get(self, request):
        return Response(UtilisateurSerializer(request.user.utilisateur).data)

    def patch(self, request):
        utilisateur = request.user.utilisateur
        serializer = MonProfilSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        est_admin = utilisateur.role_id == "admin"
        if est_admin:
            if "nom" in data:
                utilisateur.nom = data["nom"]
            if "telephone" in data:
                utilisateur.telephone = data["telephone"]
            utilisateur.save()

        nouveau_mdp = data.get("nouveau_mot_de_passe")
        if nouveau_mdp:
            request.user.set_password(nouveau_mdp)
            request.user.save()

        return Response(UtilisateurSerializer(utilisateur).data)


class AjustementStockView(APIView):
    """
    POST /api/ajustements-stock/ — corrige un stock avec motif obligatoire.
    GET  /api/ajustements-stock/ — historique complet, pour la traçabilité.
    """
    permission_classes = [EstAdmin]

    def post(self, request):
        serializer = AjustementStockCreationSerializer(
            data=request.data, context={"utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        ajustement = serializer.save()
        return Response(AjustementStockLectureSerializer(ajustement).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = AjustementStock.objects.select_related("produit", "utilisateur").order_by("-date_heure")
        return Response(AjustementStockLectureSerializer(qs, many=True).data)


class AnnulerVenteView(APIView):
    """
    POST /api/ventes/<id>/annuler/ — annule une vente : restitue le
    stock de chaque ligne, marque la transaction "annulee". Ne
    supprime jamais la transaction elle-même (traçabilité : on doit
    toujours pouvoir voir qu'une vente a existé puis a été annulée,
    pas la faire disparaître comme si de rien n'était).
    """
    permission_classes = [EstAdmin]

    def post(self, request, pk):
        # select_for_update() exige une transaction déjà ouverte — tout
        # le corps de la vue doit donc être dans le bloc atomic (bug
        # préexistant corrigé ici : ATOMIC_REQUESTS n'est pas activé dans
        # ce projet, donc select_for_update() hors atomic() lève
        # TransactionManagementError dès qu'on l'exerce réellement).
        with transaction.atomic():
            try:
                transaction_caisse = TransactionCaisse.objects.select_for_update().get(pk=pk)
            except TransactionCaisse.DoesNotExist:
                return Response({"detail": "Vente introuvable."}, status=status.HTTP_404_NOT_FOUND)

            if transaction_caisse.annulee:
                return Response({"detail": "Cette vente est déjà annulée."}, status=status.HTTP_400_BAD_REQUEST)

            vente = getattr(transaction_caisse, "venteproduits", None)
            if vente is not None:
                # Restitution du stock ligne par ligne, avec le même
                # verrouillage que la création de vente — cohérent des
                # deux côtés de la même opération.
                for ligne in vente.lignes.select_related("produit"):
                    produit = Produit.objects.select_for_update().get(pk=ligne.produit_id)
                    produit.quantite_stock = F("quantite_stock") + ligne.quantite
                    produit.save(update_fields=["quantite_stock"])

            transaction_caisse.annulee = True
            transaction_caisse.save(update_fields=["annulee"])
            transaction_caisse.refresh_from_db()

        return Response(TransactionCaisseLectureSerializer(transaction_caisse).data)


class RetourVenteView(APIView):
    """
    POST /api/ventes/<id>/retour/ — retourne tout ou partie d'une vente
    (remboursement partiel, restock). Même niveau de permission que
    AnnulerVenteView/RemboursementCreditView : toute opération qui défait
    de l'argent déjà encaissé est réservée à l'admin.
    """
    permission_classes = [EstAdmin]

    def post(self, request, pk):
        try:
            vente = TransactionCaisse.objects.select_related("client").get(pk=pk)
        except TransactionCaisse.DoesNotExist:
            return Response({"detail": "Vente introuvable."}, status=status.HTTP_404_NOT_FOUND)

        serializer = RetourVenteCreationSerializer(
            data=request.data, context={"vente": vente, "utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        vente.refresh_from_db()
        return Response(TransactionCaisseLectureSerializer(vente).data, status=status.HTTP_201_CREATED)


class RemboursementCreditView(APIView):
    """
    POST /api/remboursements-credit/ — un client rembourse (tout ou partie de) sa dette.
    GET  /api/remboursements-credit/?client=<id> — historique des remboursements
    d'un client précis, pour sa fiche détail.
    """
    permission_classes = [EstAdmin]

    def post(self, request):
        serializer = RemboursementCreditSerializer(
            data=request.data, context={"utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        client = serializer.save()
        return Response(ClientSerializer(client).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = RemboursementCredit.objects.select_related("client", "utilisateur")
        client_id = request.query_params.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)
        return Response(RemboursementCreditLectureSerializer(qs, many=True).data)


class PaiementFournisseurView(APIView):
    """
    POST /api/paiements-fournisseur/ — règle (tout ou partie de) la dette
    envers un fournisseur. Miroir de RemboursementCreditView côté achats.
    GET  /api/paiements-fournisseur/?fournisseur=<id> — historique des
    paiements d'un fournisseur précis, pour sa fiche détail.
    """
    permission_classes = [EstAdmin]

    def post(self, request):
        serializer = PaiementFournisseurSerializer(
            data=request.data, context={"utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        fournisseur = serializer.save()
        return Response(FournisseurSerializer(fournisseur).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = PaiementFournisseur.objects.select_related("fournisseur", "utilisateur")
        fournisseur_id = request.query_params.get("fournisseur")
        if fournisseur_id:
            qs = qs.filter(fournisseur_id=fournisseur_id)
        return Response(PaiementFournisseurLectureSerializer(qs, many=True).data)


from .models import ArchiveCreanceClient, ArchiveCreanceFournisseur


class ArchiverCreanceClientView(APIView):
    """
    POST /api/clients/<id>/archiver-creance/ — clôt le cycle de créance
    courant d'un client (exige solde_credit == 0 : on n'archive jamais
    une dette encore due). Ne touche pas au solde (déjà à zéro) :
    enregistre juste une photo horodatée des totaux du cycle qui vient
    de se terminer, pour repartir sur une liste propre côté UI (voir
    ArchiveCreanceClient dans models.py) sans perdre l'historique.
    GET renvoie les cycles déjà clôturés pour ce client (le plus
    récent en premier).
    """
    permission_classes = [EstAdmin]

    def post(self, request, pk):
        try:
            client = Client.objects.get(pk=pk)
        except Client.DoesNotExist:
            return Response({"detail": "Client introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if client.solde_credit != 0:
            return Response(
                {"detail": "Le solde doit être à zéro avant d'archiver cette créance."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        derniere_archive = client.archives_creance.first()  # Meta.ordering = -date_archivage
        depuis = derniere_archive.date_archivage if derniere_archive else None

        ventes_qs = TransactionCaisse.objects.filter(client=client, annulee=False)
        if depuis:
            ventes_qs = ventes_qs.filter(date_heure__gt=depuis)

        total_mis_a_credit = 0
        for vente in ventes_qs:
            if vente.mode_paiement == ModePaiement.CREDIT:
                total_mis_a_credit += vente.montant_total
            elif vente.mode_paiement == ModePaiement.MIXTE:
                total_mis_a_credit += vente.paiements.filter(mode_paiement=ModePaiement.CREDIT).aggregate(
                    total=Coalesce(Sum("montant"), 0, output_field=DecimalField())
                )["total"]

        remboursements_qs = RemboursementCredit.objects.filter(client=client)
        if depuis:
            remboursements_qs = remboursements_qs.filter(date_heure__gt=depuis)
        total_rembourse = remboursements_qs.aggregate(
            total=Coalesce(Sum("montant"), 0, output_field=DecimalField())
        )["total"]

        archive = ArchiveCreanceClient.objects.create(
            client=client,
            total_mis_a_credit=total_mis_a_credit,
            total_rembourse=total_rembourse,
            utilisateur=request.user.utilisateur,
        )
        return Response(ArchiveCreanceClientSerializer(archive).data, status=status.HTTP_201_CREATED)

    def get(self, request, pk):
        qs = ArchiveCreanceClient.objects.filter(client_id=pk).select_related("utilisateur")
        return Response(ArchiveCreanceClientSerializer(qs, many=True).data)


class ArchiverCreanceFournisseurView(APIView):
    """Miroir de ArchiverCreanceClientView côté fournisseurs (solde_du)."""
    permission_classes = [EstAdmin]

    def post(self, request, pk):
        try:
            fournisseur = Fournisseur.objects.get(pk=pk)
        except Fournisseur.DoesNotExist:
            return Response({"detail": "Fournisseur introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if fournisseur.solde_du != 0:
            return Response(
                {"detail": "Le solde doit être à zéro avant d'archiver cette créance."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        derniere_archive = fournisseur.archives_creance.first()
        depuis = derniere_archive.date_archivage if derniere_archive else None

        receptions_qs = Approvisionnement.objects.filter(fournisseur=fournisseur)
        if depuis:
            receptions_qs = receptions_qs.filter(date_reception__gt=depuis)
        total_recu = receptions_qs.aggregate(
            total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField())
        )["total"]

        paiements_qs = PaiementFournisseur.objects.filter(fournisseur=fournisseur)
        if depuis:
            paiements_qs = paiements_qs.filter(date_heure__gt=depuis)
        total_paye = paiements_qs.aggregate(
            total=Coalesce(Sum("montant"), 0, output_field=DecimalField())
        )["total"]

        archive = ArchiveCreanceFournisseur.objects.create(
            fournisseur=fournisseur,
            total_recu_a_credit=total_recu,
            total_paye=total_paye,
            utilisateur=request.user.utilisateur,
        )
        return Response(ArchiveCreanceFournisseurSerializer(archive).data, status=status.HTTP_201_CREATED)

    def get(self, request, pk):
        qs = ArchiveCreanceFournisseur.objects.filter(fournisseur_id=pk).select_related("utilisateur")
        return Response(ArchiveCreanceFournisseurSerializer(qs, many=True).data)


class SessionCaisseOuvrirView(APIView):
    """POST /api/sessions-caisse/ouvrir/ — ouvre une session de caisse pour l'utilisateur connecté."""
    permission_classes = [EstCaissierOuAdmin]

    def post(self, request):
        serializer = SessionCaisseOuvertureSerializer(
            data=request.data, context={"utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        session = serializer.save()
        return Response(SessionCaisseLectureSerializer(session).data, status=status.HTTP_201_CREATED)


class SessionCaisseCouranteView(APIView):
    """
    GET /api/sessions-caisse/courante/ — session ouverte de l'utilisateur
    connecté, avec ses totaux recalculés en direct. Réponse vide (204) si
    aucune session n'est ouverte.
    """
    permission_classes = [EstCaissierOuAdmin]

    def get(self, request):
        session = SessionCaisse.objects.filter(
            utilisateur=request.user.utilisateur, statut=StatutSession.OUVERTE
        ).first()
        if session is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        data = SessionCaisseLectureSerializer(session).data
        data.update({k: str(v) for k, v in calculer_totaux_session(session).items()})
        return Response(data)


class SessionCaisseFermerView(APIView):
    """
    POST /api/sessions-caisse/<id>/fermer/ — ferme une session et fige son
    rapport Z. Un admin peut fermer la session (oubliée) d'un autre caissier ;
    un caissier ne peut fermer que la sienne.
    """
    permission_classes = [EstCaissierOuAdmin]

    def post(self, request, pk):
        # select_for_update() exige une transaction déjà ouverte — tout
        # le corps de la vue doit donc être dans le bloc atomic, pas
        # seulement la partie qui appelle .save().
        with transaction.atomic():
            try:
                session = SessionCaisse.objects.select_for_update().get(pk=pk)
            except SessionCaisse.DoesNotExist:
                return Response({"detail": "Session introuvable."}, status=status.HTTP_404_NOT_FOUND)

            est_admin = request.user.utilisateur.role_id == "admin"
            if not est_admin and session.utilisateur_id != request.user.utilisateur.id:
                return Response({"detail": "Ce n'est pas ta session de caisse."}, status=status.HTTP_403_FORBIDDEN)
            if session.statut == StatutSession.FERMEE:
                return Response({"detail": "Cette session est déjà fermée."}, status=status.HTTP_400_BAD_REQUEST)

            serializer = SessionCaisseFermetureSerializer(session, data=request.data)
            serializer.is_valid(raise_exception=True)
            session = serializer.save()
        return Response(SessionCaisseLectureSerializer(session).data)


class SessionCaisseListView(APIView):
    """
    GET /api/sessions-caisse/ — historique des sessions. Un caissier ne voit
    que les siennes ; un admin voit tout (filtrable par ?utilisateur=<id>).
    """
    permission_classes = [EstCaissierOuAdmin]

    def get(self, request):
        qs = SessionCaisse.objects.select_related("utilisateur").order_by("-date_ouverture")
        est_admin = request.user.utilisateur.role_id == "admin"
        if est_admin:
            utilisateur_id = request.query_params.get("utilisateur")
            if utilisateur_id:
                qs = qs.filter(utilisateur_id=utilisateur_id)
        else:
            qs = qs.filter(utilisateur=request.user.utilisateur)
        return Response(SessionCaisseLectureSerializer(qs, many=True).data)


from rest_framework.throttling import ScopedRateThrottle
from django.conf import settings


class ConnexionView(ObtainAuthToken):
    """
    Endpoint de connexion : POST /api/connexion/ avec {username, password}
    renvoie un token + les infos du compte (rôle, nom, permissions).
    Protégé par rate limiting (anti brute-force) et renouvellement automatique
    des jetons expirés.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'connexion'

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        token, created = Token.objects.get_or_create(user=user)
        expire_days = getattr(settings, 'TOKEN_EXPIRE_DAYS', 14)
        if not created and token.created < timezone.now() - timedelta(days=expire_days):
            token.delete()
            token = Token.objects.create(user=user)

        utilisateur = getattr(user, "utilisateur", None)
        return Response({
            "token": token.key,
            "nom": utilisateur.nom if utilisateur else user.username,
            "role": utilisateur.role_id if utilisateur else None,
            "permissions_supplementaires": utilisateur.permissions_supplementaires if utilisateur else [],
        })