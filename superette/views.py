# fichier: superette/views.py
# Fichier déjà existant (créé par startapp) — on REMPLACE son contenu

from rest_framework import viewsets, status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from .models import Categorie, Produit, Client, Fournisseur, Service, TransactionCaisse, Approvisionnement, Depense, Role, Utilisateur
from .serializers import (
    CategorieSerializer, ProduitSerializer, ClientSerializer,
    FournisseurSerializer, ServiceSerializer,
    VenteCreationSerializer, TransactionCaisseLectureSerializer,
    ApprovisionnementCreationSerializer, ApprovisionnementLectureSerializer,
    PrestationCreationSerializer, PrestationLectureSerializer,
    DepenseSerializer, RoleSerializer, UtilisateurSerializer, UtilisateurCreationSerializer,
    AjustementStockCreationSerializer, AjustementStockLectureSerializer,
    RemboursementCreditSerializer, ModifierPermissionsSerializer, MonProfilSerializer,
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
        ).select_related("utilisateur", "client").prefetch_related("venteproduits__lignes__produit")
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
        serializer = ApprovisionnementLectureSerializer(qs, many=True)
        return Response(serializer.data)


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


class RapportJournalierView(APIView):
    """
    GET /api/rapports/?date=2026-08-16   (date optionnelle, défaut = aujourd'hui)
    """
    permission_classes = [PeutVoirRapports]

    def get(self, request):
        date_param = request.query_params.get("date")
        jour = date_cls.fromisoformat(date_param) if date_param else timezone.localdate()

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

        chiffre_affaires = ca_ventes + ca_prestations
        resultat_net = chiffre_affaires - total_depenses

        return Response({
            "date": jour.isoformat(),
            "chiffre_affaires_ventes": ca_ventes,
            "chiffre_affaires_prestations": ca_prestations,
            "chiffre_affaires_total": chiffre_affaires,
            "marge_brute_ventes": marge_ventes,
            "total_depenses": total_depenses,
            "resultat_net": resultat_net,
            "top_produits": list(top_produits),
            "produits_stock_bas": list(produits_stock_bas),
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

        def evolution_pct(actuel, ancien):
            actuel, ancien = float(actuel), float(ancien)
            if ancien == 0:
                return None  # pas de base de comparaison valable
            return round((actuel - ancien) / ancien * 100, 1)

        donnees["comparaison"] = {
            "chiffre_affaires_precedent": precedent["chiffre_affaires_total"],
            "resultat_net_precedent": precedent["resultat_net"],
            "evolution_chiffre_affaires_pct": evolution_pct(
                donnees["chiffre_affaires_total"], precedent["chiffre_affaires_total"]),
            "evolution_resultat_net_pct": evolution_pct(
                donnees["resultat_net"], precedent["resultat_net"]),
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
        try:
            transaction_caisse = TransactionCaisse.objects.select_for_update().get(pk=pk)
        except TransactionCaisse.DoesNotExist:
            return Response({"detail": "Vente introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if transaction_caisse.annulee:
            return Response({"detail": "Cette vente est déjà annulée."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
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


class RemboursementCreditView(APIView):
    """POST /api/remboursements-credit/ — un client rembourse (tout ou partie de) sa dette."""
    permission_classes = [EstAdmin]

    def post(self, request):
        serializer = RemboursementCreditSerializer(
            data=request.data, context={"utilisateur": request.user.utilisateur}
        )
        serializer.is_valid(raise_exception=True)
        client = serializer.save()
        return Response(ClientSerializer(client).data, status=status.HTTP_201_CREATED)


class ConnexionView(ObtainAuthToken):
    """
    Endpoint de connexion : POST /api/connexion/ avec {username, password}
    renvoie un token + quelques infos utiles pour que Flutter sache
    tout de suite à qui il parle (rôle, nom) sans requête supplémentaire.
    """

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        utilisateur = getattr(user, "utilisateur", None)
        return Response({
            "token": token.key,
            "nom": utilisateur.nom if utilisateur else user.username,
            "role": utilisateur.role_id if utilisateur else None,
        })