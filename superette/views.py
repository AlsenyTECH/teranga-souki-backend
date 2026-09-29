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
from django.db.models import ProtectedError, Sum, F, Value, Case, When, DecimalField
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAuthenticated

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
    RetourApproCreationSerializer,
    PrestationCreationSerializer, PrestationLectureSerializer,
    DepenseSerializer, RoleSerializer, UtilisateurSerializer, UtilisateurCreationSerializer,
    AjustementStockCreationSerializer, AjustementStockLectureSerializer,
    RemboursementCreditSerializer, RemboursementCreditLectureSerializer,
    ModifierPermissionsSerializer, MonProfilSerializer, UtilisateurEditionSerializer,
    ArchiveCreanceClientSerializer, ArchiveCreanceFournisseurSerializer,
    PaiementFournisseurSerializer, PaiementFournisseurLectureSerializer,
    SessionCaisseOuvertureSerializer, SessionCaisseFermetureSerializer,
    SessionCaisseLectureSerializer, calculer_totaux_session, part_especes,
    RetourVenteCreationSerializer, RetourVenteLectureSerializer,
)
from .pagination import StandardPagination
from .permissions import (
    LectureAdminEcritureAdmin, EstCaissierOuAdmin, EstAdmin,
    PeutGererCatalogue, PeutGererFournisseurs, PeutGererClients,
    PeutGererApprovisionnement, PeutGererDepenses, PeutVoirRapports,
)


class SuppressionProtegeeMixin:
    """DELETE sur un élément déjà référencé (produit vendu, client avec
    historique...) : 400 explicite au lieu d'une erreur 500 ProtectedError."""

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": "Impossible de supprimer : cet élément est déjà utilisé dans l'historique "
                           "(ventes, réceptions...). Désactive-le ou garde-le."},
                status=status.HTTP_400_BAD_REQUEST,
            )


def filtrer_par_dates(qs, request, champ):
    """?date_debut=AAAA-MM-JJ&date_fin=AAAA-MM-JJ (bornes incluses, chacune
    optionnelle) — pour ne plus renvoyer tout l'historique à chaque écran."""
    debut = request.query_params.get("date_debut")
    fin = request.query_params.get("date_fin")
    if debut:
        qs = qs.filter(**{f"{champ}__date__gte": parse_date(debut, "date_debut")})
    if fin:
        qs = qs.filter(**{f"{champ}__date__lte": parse_date(fin, "date_fin")})
    return qs


def reponse_liste(request, view, qs, serializer_class):
    """Même contrat que StandardPagination pour les APIView : liste complète
    par défaut (rétrocompatible), page {count, next, previous, results}
    dès que ?page ou ?page_size est fourni."""
    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs, request, view=view)
    if page is not None:
        return paginator.get_paginated_response(serializer_class(page, many=True).data)
    return Response(serializer_class(qs, many=True).data)


class CategorieViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
    # ModelViewSet donne automatiquement les 5 actions REST standard :
    # list (GET /categories/), retrieve (GET /categories/1/),
    # create (POST), update (PUT/PATCH), destroy (DELETE)
    queryset = Categorie.objects.all()
    serializer_class = CategorieSerializer
    permission_classes = [PeutGererCatalogue]


class ProduitViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
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


class ClientViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [PeutGererClients]


class FournisseurViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
    queryset = Fournisseur.objects.all()
    serializer_class = FournisseurSerializer
    permission_classes = [PeutGererFournisseurs]


class ServiceViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
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
        qs = TransactionCaisse.objects.filter(
            venteproduits__isnull=False
        ).select_related("utilisateur", "client", "venteproduits").prefetch_related(
            "venteproduits__lignes__produit", "venteproduits__lignes__retours", "retours", "paiements",
        ).order_by("-date_heure")
        # ?client=<id> : historique des ventes d'un client précis, pour
        # sa fiche détail (Flutter fusionne ce résultat avec ses
        # remboursements de crédit pour construire une timeline unique).
        client_id = request.query_params.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)
        # Un caissier ne voit que SES ventes — sauf la fiche d'un client
        # s'il a reçu la permission "clients", où l'historique complet du
        # client est justement l'objet de l'écran.
        utilisateur = request.user.utilisateur
        if utilisateur.role_id != "admin":
            voit_client = client_id and "clients" in (utilisateur.permissions_supplementaires or [])
            if not voit_client:
                qs = qs.filter(utilisateur=utilisateur)
        qs = filtrer_par_dates(qs, request, "date_heure")
        return reponse_liste(request, self, qs, TransactionCaisseLectureSerializer)


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
        qs = Approvisionnement.objects.select_related("fournisseur").prefetch_related(
            "lignes__produit", "lignes__retours", "retours__utilisateur",
        ).order_by("-date_reception")
        # ?fournisseur=<id> : historique des réceptions d'un fournisseur
        # précis, pour sa fiche détail.
        fournisseur_id = request.query_params.get("fournisseur")
        if fournisseur_id:
            qs = qs.filter(fournisseur_id=fournisseur_id)
        qs = filtrer_par_dates(qs, request, "date_reception")
        return reponse_liste(request, self, qs, ApprovisionnementLectureSerializer)


class ApprovisionnementDetailView(APIView):
    """
    PATCH/DELETE /api/approvisionnements/<pk>/ — corrige ou annule une
    réception saisie par erreur. Refuse (400) si l'un de ses produits a
    bougé depuis, si elle a déjà un retour, ou si elle est déjà annulée
    (voir serializers.verifier_appro_modifiable) : le CUMP et le stock
    actuels dépendraient alors de mouvements qu'on ne peut plus démêler
    proprement. DELETE annule (garde la ligne, marque annulee=True,
    défait son effet) plutôt que de supprimer réellement la ligne — même
    logique d'audit que TransactionCaisse.annulee côté vente.
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
            appro.annulee = True
            appro.save(update_fields=["annulee"])
        out = ApprovisionnementLectureSerializer(appro)
        return Response(out.data)


class RetourApproView(APIView):
    """
    POST /api/approvisionnements/<pk>/retour/ — retour partiel de
    marchandise au fournisseur (miroir de RetourVenteView côté achats).
    """
    permission_classes = [PeutGererApprovisionnement]

    def post(self, request, pk):
        with transaction.atomic():
            appro = get_object_or_404(Approvisionnement.objects.select_for_update(), pk=pk)
            serializer = RetourApproCreationSerializer(
                data=request.data,
                context={"appro": appro, "utilisateur": request.user.utilisateur},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
        appro.refresh_from_db()
        out = ApprovisionnementLectureSerializer(appro)
        return Response(out.data, status=status.HTTP_201_CREATED)


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
        ).select_related("utilisateur", "client", "prestationservice__service").order_by("-date_heure")
        if request.user.utilisateur.role_id != "admin":
            qs = qs.filter(utilisateur=request.user.utilisateur)
        qs = filtrer_par_dates(qs, request, "date_heure")
        return reponse_liste(request, self, qs, PrestationLectureSerializer)


class DepenseViewSet(SuppressionProtegeeMixin, viewsets.ModelViewSet):
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
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from .models import (
    LigneVente, Depense as DepenseModel, AjustementStock, ModePaiement,
    RetourVente, LigneRetour, VenteProduits, PaiementVente,
)


def evolution_pct(actuel, ancien):
    """% d'évolution entre deux valeurs — None si pas de base de
    comparaison valable (période précédente à 0), plutôt qu'une fausse
    division. Partagé entre RapportJournalierView et RapportPeriodeView."""
    actuel, ancien = float(actuel), float(ancien)
    if ancien == 0:
        return None
    return round((actuel - ancien) / ancien * 100, 1)


def parse_date(valeur, nom):
    """Date ISO (YYYY-MM-DD) d'un paramètre de requête, ou 400 explicite —
    jamais une erreur 500 sur une date mal formée (ex: ?date=2026-13-01)."""
    try:
        return date_cls.fromisoformat(valeur)
    except (TypeError, ValueError):
        raise ValidationError({nom: "Format de date invalide (attendu : AAAA-MM-JJ)."})


def calculer_totaux_periode(debut, fin):
    """
    Totaux financiers entre deux dates incluses — partagé entre le rapport
    journalier et le rapport de période, pour que les deux donnent toujours
    les mêmes chiffres pour le même jour.

    - Chiffre d'affaires ventes = encaissé (réductions déjà déduites) MOINS
      les retours enregistrés sur la période (un retour réduit le CA du jour
      où il a lieu, pas celui de la vente d'origine).
    - Marge = Σ (prix - CUMP actuel) x quantité, moins les réductions
      accordées, moins la marge rendue par les retours. Approximation
      assumée : CUMP actuel, pas celui du jour de la vente (non historisé).
    """
    transactions = TransactionCaisse.objects.filter(
        date_heure__date__gte=debut, date_heure__date__lte=fin, annulee=False
    )
    ventes_qs = transactions.filter(venteproduits__isnull=False)
    prestations_qs = transactions.filter(prestationservice__isnull=False)

    ca_ventes_brut = ventes_qs.aggregate(total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField()))["total"]
    ca_prestations = prestations_qs.aggregate(total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField()))["total"]

    retours_qs = RetourVente.objects.filter(
        date_heure__date__gte=debut, date_heure__date__lte=fin, vente__annulee=False
    )
    total_retours = retours_qs.aggregate(total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField()))["total"]
    ca_ventes = ca_ventes_brut - total_retours

    marge_expr = ExpressionWrapper(
        (F("prix_unitaire_vente") - F("produit__prix_achat_moyen")) * F("quantite"),
        output_field=DecimalField(max_digits=14, decimal_places=5),
    )
    marge_lignes = LigneVente.objects.filter(
        vente__transaction__date_heure__date__gte=debut,
        vente__transaction__date_heure__date__lte=fin,
        vente__transaction__annulee=False,
    ).aggregate(total=Coalesce(Sum(marge_expr), 0, output_field=DecimalField()))["total"]
    total_reductions = VenteProduits.objects.filter(transaction__in=ventes_qs).aggregate(
        total=Coalesce(Sum("reduction_montant"), 0, output_field=DecimalField())
    )["total"]
    cout_retours = LigneRetour.objects.filter(retour__in=retours_qs).aggregate(
        total=Coalesce(
            Sum(ExpressionWrapper(
                F("quantite") * F("ligne_vente__produit__prix_achat_moyen"),
                output_field=DecimalField(max_digits=14, decimal_places=5),
            )),
            0, output_field=DecimalField(),
        )
    )["total"]
    marge = marge_lignes - total_reductions - (total_retours - cout_retours)

    total_depenses = DepenseModel.objects.filter(
        date_depense__gte=debut, date_depense__lte=fin
    ).aggregate(total=Coalesce(Sum("montant"), 0, output_field=DecimalField()))["total"]

    chiffre_affaires_total = ca_ventes + ca_prestations
    deux = Decimal("0.01")
    return {
        "transactions": transactions,
        "ventes_qs": ventes_qs,
        "chiffre_affaires_ventes": Decimal(ca_ventes).quantize(deux),
        "chiffre_affaires_prestations": Decimal(ca_prestations).quantize(deux),
        "chiffre_affaires_total": Decimal(chiffre_affaires_total).quantize(deux),
        "retours_ventes": Decimal(total_retours).quantize(deux),
        "reductions_accordees": Decimal(total_reductions).quantize(deux),
        "marge_brute_ventes": Decimal(marge).quantize(deux),
        "total_depenses": Decimal(total_depenses).quantize(deux),
        "resultat_net": Decimal(chiffre_affaires_total - total_depenses).quantize(deux),
    }


def _sans_querysets(totaux):
    return {k: v for k, v in totaux.items() if k not in ("transactions", "ventes_qs")}


class RapportJournalierView(APIView):
    """
    GET /api/rapports/?date=2026-08-16   (date optionnelle, défaut = aujourd'hui)
    """
    permission_classes = [PeutVoirRapports]

    def get(self, request):
        date_param = request.query_params.get("date")
        jour = parse_date(date_param, "date") if date_param else timezone.localdate()

        totaux = _sans_querysets(calculer_totaux_periode(jour, jour))
        veille = jour - timedelta(days=1)
        totaux_veille = _sans_querysets(calculer_totaux_periode(veille, veille))

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
        debut = parse_date(debut_str, "debut")
        fin = parse_date(fin_str, "fin")
        if fin < debut:
            return Response({"detail": "La date de fin doit être après la date de début."}, status=status.HTTP_400_BAD_REQUEST)

        donnees = self._calculer(debut, fin)

        # Comparaison avec la période précédente de MÊME durée, juste
        # avant — c'est ce qui donne un sens à "87 350 F" : est-ce
        # mieux ou moins bien que d'habitude ?
        duree = (fin - debut).days + 1
        debut_precedent = debut - timedelta(days=duree)
        fin_precedent = debut - timedelta(days=1)
        precedent = _sans_querysets(calculer_totaux_periode(debut_precedent, fin_precedent))

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
        totaux = calculer_totaux_periode(debut, fin)
        transactions = totaux["transactions"]
        ventes_qs = totaux["ventes_qs"]
        nb_transactions = transactions.count()
        nb_ventes = ventes_qs.count()
        ca_ventes = totaux["chiffre_affaires_ventes"]
        panier_moyen = round(float(ca_ventes) / nb_ventes, 2) if nb_ventes > 0 else 0

        # Répartition par mode de paiement — utile pour anticiper les
        # besoins de monnaie/liquidités et suivre l'adoption du mobile money.
        # Une vente mixte est ventilée sur ses modes réels (espèces, Wave...)
        # au lieu d'apparaître dans une catégorie "mixte" opaque.
        repartition_paiement = {}
        for mode, _ in ModePaiement.choices:
            if mode == ModePaiement.MIXTE:
                continue
            direct = transactions.filter(mode_paiement=mode).aggregate(
                total=Coalesce(Sum("montant_total"), 0, output_field=DecimalField())
            )["total"]
            via_mixte = PaiementVente.objects.filter(
                vente__in=transactions.filter(mode_paiement=ModePaiement.MIXTE), mode_paiement=mode
            ).aggregate(total=Coalesce(Sum("montant"), 0, output_field=DecimalField()))["total"]
            repartition_paiement[mode] = direct + via_mixte

        lignes_periode = LigneVente.objects.filter(
            vente__transaction__date_heure__date__gte=debut,
            vente__transaction__date_heure__date__lte=fin,
            vente__transaction__annulee=False,
        )
        top_produits = list(
            lignes_periode
            .values("produit__nom")
            .annotate(quantite_totale=Sum("quantite"),
                      chiffre_affaires=Sum(F("quantite") * F("prix_unitaire_vente")))
            .order_by("-quantite_totale")[:10]
        )

        # Répartition du chiffre d'affaires par catégorie de produit —
        # matière première du graphique donut côté app.
        ventes_par_categorie = list(
            lignes_periode
            .values(categorie=F("produit__categorie__libelle"))
            .annotate(chiffre_affaires=Sum(F("quantite") * F("prix_unitaire_vente")))
            .order_by("-chiffre_affaires")
        )

        # Produits actifs n'ayant fait l'objet d'AUCUNE vente (non annulée)
        # sur la période — signal utile pour repérer le stock qui dort.
        produits_vendus_ids = lignes_periode.values_list("produit_id", flat=True).distinct()
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
            **_sans_querysets(totaux),
            "nombre_transactions": nb_transactions,
            "panier_moyen": panier_moyen,
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

        # Le compte du patron principal ne se modifie que par lui-même (via
        # /mon-profil/) : sinon n'importe quel autre admin pourrait changer
        # son mot de passe ou son identifiant et lui prendre la superette.
        if utilisateur.est_compte_principal and utilisateur.compte_id != request.user.id:
            return Response(
                {"detail": "Le compte du patron principal ne peut être modifié que par lui-même."},
                status=status.HTTP_403_FORBIDDEN,
            )

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
        if nouveau_mdp:
            # Nouveau mot de passe = toutes les sessions ouvertes de ce compte
            # sont coupées (ex: mot de passe volé, employé qui part).
            Token.objects.filter(user=utilisateur.compte).delete()

        return Response(UtilisateurSerializer(utilisateur).data)

    def delete(self, request, pk):
        try:
            utilisateur = Utilisateur.objects.select_related("compte").get(pk=pk)
        except Utilisateur.DoesNotExist:
            return Response({"detail": "Compte introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if utilisateur.est_compte_principal:
            return Response(
                {"detail": "Le compte du patron principal ne peut pas être supprimé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if utilisateur.compte_id == request.user.id:
            return Response(
                {"detail": "Tu ne peux pas supprimer ton propre compte — demande à un autre admin."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                # CASCADE depuis compte (voir Utilisateur.compte) : supprime
                # le profil métier avec le compte de connexion.
                utilisateur.compte.delete()
            return Response({"action": "supprime"})
        except ProtectedError:
            # Ce compte a déjà de l'activité enregistrée (ventes, sessions
            # de caisse, ajustements...) — les nombreuses FK PROTECT vers
            # Utilisateur empêchent une suppression réelle sans perdre la
            # traçabilité de cet historique. Repli sur une désactivation
            # (bloque la connexion, garde tout l'historique intact) — même
            # principe que ProduitsBody.tsx pour un produit déjà utilisé.
            utilisateur.compte.is_active = False
            utilisateur.compte.save(update_fields=["is_active"])
            Token.objects.filter(user=utilisateur.compte).delete()
            utilisateur.actif = False
            utilisateur.save(update_fields=["actif"])
            return Response({"action": "desactive", "utilisateur": UtilisateurSerializer(utilisateur).data})


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
        serializer = MonProfilSerializer(data=request.data, context={"utilisateur": utilisateur})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        est_admin = utilisateur.role_id == "admin"
        if est_admin:
            if "nom" in data:
                utilisateur.nom = data["nom"]
            if "telephone" in data:
                utilisateur.telephone = data["telephone"]
            utilisateur.save()

        reponse = UtilisateurSerializer(utilisateur).data
        nouveau_mdp = data.get("nouveau_mot_de_passe")
        if nouveau_mdp:
            request.user.set_password(nouveau_mdp)
            request.user.save()
            # Coupe les sessions ouvertes ailleurs (autre téléphone, poste
            # oublié) et renvoie un nouveau jeton pour l'appareil courant.
            Token.objects.filter(user=request.user).delete()
            reponse = {**reponse, "token": Token.objects.create(user=request.user).key}

        return Response(reponse)


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
    POST /api/ventes/<id>/annuler/ — annule une vente ou une prestation et
    défait TOUS ses effets encore en place :
    - stock : on ne remet que la quantité pas déjà rendue par un retour ;
    - crédit client : on retire la part à crédit pas déjà déduite par un retour ;
    - points fidélité gagnés sur cette vente (jamais sous zéro) ;
    - caisse : les espèces encore dues au client sortent de la session de
      la personne qui annule (ou de la session d'origine si elle est encore
      ouverte), et sont soustraites de son rapport Z.
    Ne supprime jamais la transaction (traçabilité).
    """
    permission_classes = [EstAdmin]

    def post(self, request, pk):
        # select_for_update() exige une transaction déjà ouverte — tout
        # le corps de la vue doit donc être dans le bloc atomic.
        with transaction.atomic():
            try:
                transaction_caisse = TransactionCaisse.objects.select_for_update().get(pk=pk)
            except TransactionCaisse.DoesNotExist:
                return Response({"detail": "Vente introuvable."}, status=status.HTTP_404_NOT_FOUND)

            if transaction_caisse.annulee:
                return Response({"detail": "Cette vente est déjà annulée."}, status=status.HTTP_400_BAD_REQUEST)

            retours = list(transaction_caisse.retours.all())
            montant_retourne = sum((r.montant_total for r in retours), Decimal("0"))

            # Espèces encore à rendre = part espèces - ce que les retours ont déjà rendu en liquide.
            especes_a_rendre = part_especes(transaction_caisse) - sum(
                (r.montant_total for r in retours if r.affecte_caisse), Decimal("0")
            )
            especes_a_rendre = max(especes_a_rendre, Decimal("0"))

            session_annulation = SessionCaisse.objects.filter(
                utilisateur=request.user.utilisateur, statut=StatutSession.OUVERTE
            ).first()
            session_origine = transaction_caisse.session_caisse
            if session_annulation is None and session_origine and session_origine.statut == StatutSession.OUVERTE:
                session_annulation = session_origine
            if session_annulation is None and especes_a_rendre > 0:
                return Response(
                    {"detail": "Ouvre ta session de caisse pour rendre les espèces de cette vente "
                               f"({especes_a_rendre} F) avant de l'annuler."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            vente = getattr(transaction_caisse, "venteproduits", None)
            if vente is not None:
                for ligne in vente.lignes.all():
                    deja_rendue = ligne.retours.aggregate(total=Sum("quantite"))["total"] or Decimal("0")
                    a_remettre = ligne.quantite - deja_rendue
                    if a_remettre > 0:
                        produit = Produit.objects.select_for_update().get(pk=ligne.produit_id)
                        produit.quantite_stock = F("quantite_stock") + a_remettre
                        produit.save(update_fields=["quantite_stock"])

            if transaction_caisse.client_id:
                client = Client.objects.select_for_update().get(pk=transaction_caisse.client_id)
                credit_restant = Decimal("0")
                if transaction_caisse.mode_paiement == ModePaiement.CREDIT:
                    # Chaque retour a déjà déduit son montant complet de la dette.
                    credit_restant = transaction_caisse.montant_total - montant_retourne
                elif transaction_caisse.mode_paiement == ModePaiement.MIXTE:
                    ligne_credit = transaction_caisse.paiements.filter(mode_paiement=ModePaiement.CREDIT).first()
                    if ligne_credit:
                        # Même formule (et même arrondi) que RetourVenteCreationSerializer.
                        deja_deduit = sum(
                            ((ligne_credit.montant * r.montant_total / transaction_caisse.montant_total)
                             .quantize(Decimal("0.01")) for r in retours),
                            Decimal("0"),
                        )
                        credit_restant = ligne_credit.montant - deja_deduit
                if credit_restant > 0:
                    client.solde_credit = F("solde_credit") - credit_restant
                if vente is not None:
                    # Même règle que VenteCreationSerializer : 1 point par 500 F.
                    points = int(transaction_caisse.montant_total // 500)
                    if points > 0:
                        # Case plutôt que Greatest(F - points, 0) : sur MySQL la
                        # colonne est UNSIGNED, une soustraction négative y
                        # lèverait une erreur avant même le plancher à 0.
                        client.points_fidelite = Case(
                            When(points_fidelite__gte=points, then=F("points_fidelite") - points),
                            default=Value(0),
                        )
                client.save(update_fields=["solde_credit", "points_fidelite"])

            transaction_caisse.annulee = True
            transaction_caisse.date_annulation = timezone.now()
            transaction_caisse.session_annulation = session_annulation
            transaction_caisse.montant_annulation_especes = especes_a_rendre if session_annulation else Decimal("0")
            transaction_caisse.save(update_fields=[
                "annulee", "date_annulation", "session_annulation", "montant_annulation_especes",
            ])
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
        # Verrou sur la vente pendant tout le retour : une annulation ou un
        # autre retour concurrent attend la fin de celui-ci.
        with transaction.atomic():
            try:
                vente = TransactionCaisse.objects.select_for_update().get(pk=pk)
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

        # Seule la part NON payée à la réception (tranche/crédit) a créé de la
        # dette — une réception payée comptant ou annulée n'en fait pas partie.
        receptions_qs = Approvisionnement.objects.filter(fournisseur=fournisseur, annulee=False)
        if depuis:
            receptions_qs = receptions_qs.filter(date_reception__gt=depuis)
        total_recu = receptions_qs.aggregate(
            total=Coalesce(Sum(F("montant_total") - F("montant_paye")), 0, output_field=DecimalField())
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


class DeconnexionView(APIView):
    """POST /api/deconnexion/ — révoque le jeton de l'appareil : un jeton
    copié ou volé ne sert plus à rien après la déconnexion."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.auth is not None:
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        if utilisateur is not None and not utilisateur.actif:
            return Response({"detail": "Ce compte a été désactivé."}, status=status.HTTP_403_FORBIDDEN)
        return Response({
            "token": token.key,
            "nom": utilisateur.nom if utilisateur else user.username,
            "role": utilisateur.role_id if utilisateur else None,
            "permissions_supplementaires": utilisateur.permissions_supplementaires if utilisateur else [],
        })