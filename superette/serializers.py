# fichier: superette/serializers.py
# Fichier À CRÉER (n'existe pas encore dans superette/) — n'existe pas par défaut

from rest_framework import serializers
from .models import (
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur, Service, Depense,
)


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["libelle"]


class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        # Liste EXPLICITE des champs exposés — jamais fields = "__all__"
        # sur un modèle contenant un mot de passe : mot_de_passe_hash
        # ne doit JAMAIS apparaître dans une réponse JSON.
        fields = ["id", "nom", "telephone", "role", "actif", "date_creation", "permissions_supplementaires"]
        read_only_fields = ["date_creation"]


class CategorieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categorie
        fields = ["id", "libelle"]


class ProduitSerializer(serializers.ModelSerializer):
    # SerializerMethodField : un champ calculé, pas stocké en base.
    # Utilise la méthode stock_bas() qu'on a écrite dans le modèle.
    stock_bas = serializers.SerializerMethodField()
    # StringRelatedField : au lieu de renvoyer categorie: 3 (juste l'id),
    # renvoie categorie: "Boissons" (le __str__ du modèle lié) —
    # plus lisible côté Flutter sans requête supplémentaire.
    categorie_nom = serializers.StringRelatedField(source="categorie", read_only=True)

    class Meta:
        model = Produit
        fields = [
            "id", "nom", "code_barre", "prix_achat_moyen", "prix_vente",
            "unite_vente", "quantite_stock", "seuil_alerte", "categorie", "categorie_nom",
            "actif", "stock_bas",
        ]
        # prix_achat_moyen n'est JAMAIS modifiable directement depuis l'API :
        # il est recalculé automatiquement par la logique métier des
        # réceptions (chapitre suivant), jamais saisi à la main.
        read_only_fields = ["prix_achat_moyen"]

    def get_stock_bas(self, obj):
        return obj.stock_bas()

    def validate_prix_vente(self, value):
        # Validation personnalisée : un prix de vente à 0 est suspect
        # (probablement une erreur de saisie), on le bloque ici plutôt
        # que de laisser une CHECK constraint SQL renvoyer une erreur
        # 500 illisible à l'utilisateur.
        if value <= 0:
            raise serializers.ValidationError("Le prix de vente doit être strictement positif.")
        return value


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "nom", "telephone", "adresse", "solde_credit", "points_fidelite"]
        # solde_credit ne doit pas être modifiable en écriture directe
        # via l'API générale — il changera uniquement via la logique
        # métier des ventes à crédit / remboursements
        read_only_fields = ["solde_credit"]


class DepenseSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = Depense
        fields = [
            "id", "montant", "categorie", "description", "date_depense",
            "type_recurrence", "utilisateur", "utilisateur_nom",
        ]
        # utilisateur en lecture seule : il est fixé automatiquement
        # dans la vue à partir du token, jamais choisi par le client
        # (empêcherait un caissier de créer une dépense au nom de l'admin)
        read_only_fields = ["utilisateur"]


class FournisseurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fournisseur
        fields = ["id", "nom", "contact"]


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "libelle", "tarif"]


# ============================================================
# Chapitre 12 — Ventes : logique métier avec verrouillage et
# transaction atomique. À partir d'ici, les serializers ne se
# contentent plus de mapper des champs : ils portent de la logique.
# ============================================================

from django.db import transaction
from django.db.models import F
from .models import TransactionCaisse, VenteProduits, LigneVente


class LigneVenteEcritureSerializer(serializers.Serializer):
    # serializers.Serializer (pas ModelSerializer) : on ne veut PAS
    # créer directement un objet LigneVente ici, juste valider la
    # forme des données reçues. La création réelle se fait "à la main"
    # dans VenteCreationSerializer.create(), où on a besoin de vérifier
    # le stock avant de créer quoi que ce soit.
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.filter(actif=True))
    # DecimalField, pas IntegerField : un produit vendu au poids envoie
    # une quantité fractionnaire (0.750 kg), un produit à l'unité
    # envoie un entier (2) — même champ, deux usages naturels.
    quantite = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0.001)
    # prix_unitaire_vente optionnel : si absent, on utilise le prix
    # catalogue du produit (cas normal). S'il est fourni, ça permet
    # une remise ponctuelle décidée par l'admin/caissier.
    prix_unitaire_vente = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False
    )


class LigneVenteLectureSerializer(serializers.ModelSerializer):
    produit_nom = serializers.StringRelatedField(source="produit", read_only=True)
    # Propagé jusqu'au reçu/à l'historique : sans ça, le frontend ne
    # peut pas savoir s'il doit afficher "2" ou "0.750 kg".
    produit_unite_vente = serializers.CharField(source="produit.unite_vente", read_only=True)

    class Meta:
        model = LigneVente
        fields = ["id", "produit", "produit_nom", "produit_unite_vente", "quantite", "prix_unitaire_vente"]


class VenteCreationSerializer(serializers.Serializer):
    """
    Serializer d'ÉCRITURE pour créer une vente complète en un seul
    appel API : POST /api/ventes/
    {
      "client": 3,                          // optionnel
      "mode_paiement": "wave",
      "lignes": [
        {"produit": 1, "quantite": 2},
        {"produit": 5, "quantite": 1, "prix_unitaire_vente": "500.00"}
      ]
    }
    """
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True
    )
    mode_paiement = serializers.ChoiceField(choices=["especes", "wave", "orange_money", "credit"])
    lignes = LigneVenteEcritureSerializer(many=True)

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError("Une vente doit contenir au moins une ligne.")
        return value

    def validate(self, data):
        # Une vente à crédit doit pouvoir être réclamée à quelqu'un —
        # impossible de la laisser anonyme, contrairement aux autres
        # modes de paiement où l'anonymat est un choix légitime.
        if data.get("mode_paiement") == "credit" and not data.get("client"):
            raise serializers.ValidationError(
                "Une vente à crédit doit être attribuée à un client identifié (pas anonyme)."
            )
        return data

    def create(self, validated_data):
        lignes_data = validated_data.pop("lignes")
        utilisateur = self.context["utilisateur"]  # injecté depuis la vue

        # transaction.atomic() : tout le bloc réussit ensemble, ou
        # tout est annulé (rollback) au moindre problème — y compris
        # si une exception Python est levée n'importe où dans le bloc.
        with transaction.atomic():
            montant_total = 0
            lignes_pretes = []

            for ligne in lignes_data:
                # select_for_update() : verrouille la ligne PRODUIT en
                # base jusqu'à la fin de la transaction. Une deuxième
                # vente simultanée sur le même produit devra ATTENDRE
                # que cette transaction se termine avant de lire le
                # stock à son tour — élimine la course critique.
                produit = Produit.objects.select_for_update().get(pk=ligne["produit"].pk)

                if produit.quantite_stock < ligne["quantite"]:
                    # Lever une exception ICI annule tout le bloc atomic :
                    # aucune ligne déjà traitée avant celle-ci ne sera
                    # sauvegardée. C'est exactement le comportement voulu.
                    raise serializers.ValidationError(
                        f"Stock insuffisant pour {produit.nom} "
                        f"(disponible: {produit.quantite_stock}, demandé: {ligne['quantite']})"
                    )

                prix = ligne.get("prix_unitaire_vente", produit.prix_vente)
                montant_total += prix * ligne["quantite"]
                lignes_pretes.append((produit, ligne["quantite"], prix))

            transaction_caisse = TransactionCaisse.objects.create(
                montant_total=montant_total,
                mode_paiement=validated_data["mode_paiement"],
                utilisateur=utilisateur,
                client=validated_data.get("client"),
            )
            vente = VenteProduits.objects.create(transaction=transaction_caisse)

            for produit, quantite, prix in lignes_pretes:
                LigneVente.objects.create(
                    vente=vente, produit=produit, quantite=quantite, prix_unitaire_vente=prix
                )
                # F("quantite_stock") - quantite : décrémentation faite
                # DIRECTEMENT en SQL (UPDATE ... SET stock = stock - 2),
                # pas en Python (produit.quantite_stock -= 2 puis save()).
                # Différence cruciale : la version Python lit une valeur,
                # la modifie en mémoire, puis l'écrit — deux requêtes
                # concurrentes pourraient se marcher dessus même AVEC le
                # verrou. F() fait tout en une seule opération atomique
                # côté base de données.
                produit.quantite_stock = F("quantite_stock") - quantite
                produit.save(update_fields=["quantite_stock"])

            client = validated_data.get("client")
            if client is not None:
                # Règle de fidélité : 1 point par tranche de 500 F
                # dépensés. C'est la même formule que l'app affichait
                # déjà en estimation avant l'encaissement — maintenant
                # elle est réellement appliquée, pas juste montrée.
                points_gagnes = int(montant_total // 500)
                if points_gagnes > 0:
                    client.points_fidelite = F("points_fidelite") + points_gagnes
                    client.save(update_fields=["points_fidelite"])

                # Vente à crédit : rien n'est encaissé maintenant, le
                # montant s'ajoute à la dette du client au lieu d'un
                # paiement réel — remboursable plus tard (voir
                # RemboursementCreditSerializer plus bas).
                if validated_data["mode_paiement"] == "credit":
                    client.solde_credit = F("solde_credit") + montant_total
                    client.save(update_fields=["solde_credit"])

            return transaction_caisse


class TransactionCaisseLectureSerializer(serializers.ModelSerializer):
    lignes = LigneVenteLectureSerializer(source="venteproduits.lignes", many=True, read_only=True)
    caissier_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)
    client_nom = serializers.StringRelatedField(source="client", read_only=True)
    # SerializerMethodField plutôt qu'un simple source="client.telephone" :
    # évite une AttributeError quand la vente est anonyme (client=None).
    client_telephone = serializers.SerializerMethodField()
    client_adresse = serializers.SerializerMethodField()

    class Meta:
        model = TransactionCaisse
        fields = [
            "id", "date_heure", "montant_total", "mode_paiement",
            "caissier_nom", "client_nom", "client_telephone", "client_adresse", "annulee", "lignes",
        ]

    def get_client_telephone(self, obj):
        return obj.client.telephone if obj.client else None

    def get_client_adresse(self, obj):
        return obj.client.adresse if obj.client else None


# ============================================================
# Chapitre 13 — Approvisionnement : réception de marchandise
# avec recalcul automatique du CUMP (coût unitaire moyen pondéré)
# ============================================================

from decimal import Decimal
from .models import Approvisionnement, LigneAppro, Fournisseur


class LigneApproEcritureSerializer(serializers.Serializer):
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.filter(actif=True))
    quantite_recue = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0.001)
    # Mode "unité" : prix_unitaire_achat rempli directement.
    # Mode "lot" : prix_lot ET quantite_par_lot remplis à la place —
    # prix_unitaire_achat devient optionnel en entrée, il est
    # RECALCULÉ côté serveur (jamais fait confiance à un calcul fait
    # côté app, même si l'app l'affiche déjà pour l'ergonomie).
    prix_unitaire_achat = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    prix_lot = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    quantite_par_lot = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=0.001, required=False)
    # NOUVEAU, optionnel : permet de mettre à jour le prix de VENTE du
    # produit directement depuis la réception, pratique quand on
    # reçoit un produit et qu'on décide son prix de vente dans la
    # foulée plutôt que de repasser par Catalogue. Ignoré si absent —
    # le prix de vente existant reste inchangé.
    prix_vente = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)

    def validate(self, data):
        achat_lot = data.get("prix_lot") is not None or data.get("quantite_par_lot") is not None
        if achat_lot:
            if data.get("prix_lot") is None or data.get("quantite_par_lot") is None:
                raise serializers.ValidationError(
                    "Pour un achat par lot, le prix du lot ET la quantité par lot sont requis ensemble."
                )
        elif data.get("prix_unitaire_achat") is None:
            raise serializers.ValidationError("Indique un prix unitaire, ou un prix de lot avec sa quantité.")
        return data


class LigneApproLectureSerializer(serializers.ModelSerializer):
    produit_nom = serializers.StringRelatedField(source="produit", read_only=True)
    produit_unite_vente = serializers.CharField(source="produit.unite_vente", read_only=True)

    class Meta:
        model = LigneAppro
        fields = ["id", "produit", "produit_nom", "produit_unite_vente", "quantite_recue", "prix_unitaire_achat", "prix_lot", "quantite_par_lot"]


class ApprovisionnementCreationSerializer(serializers.Serializer):
    """
    POST /api/approvisionnements/
    {
      "fournisseur": 2,
      "lignes": [
        {"produit": 1, "quantite_recue": 20, "prix_unitaire_achat": "13500.00"}
      ]
    }
    """
    fournisseur = serializers.PrimaryKeyRelatedField(queryset=Fournisseur.objects.all())
    numero_facture = serializers.CharField(required=False, allow_blank=True, max_length=50)
    lignes = LigneApproEcritureSerializer(many=True)

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError("Un approvisionnement doit contenir au moins une ligne.")
        return value

    def create(self, validated_data):
        lignes_data = validated_data.pop("lignes")

        with transaction.atomic():
            appro = Approvisionnement.objects.create(
                fournisseur=validated_data["fournisseur"],
                numero_facture=validated_data.get("numero_facture", ""),
            )

            for ligne in lignes_data:
                # Même principe de verrouillage que pour la vente : on
                # bloque la ligne produit pendant tout le calcul, pour
                # qu'une vente ou une autre réception simultanée sur ce
                # même produit ne lise pas un stock/prix "entre deux".
                produit = Produit.objects.select_for_update().get(pk=ligne["produit"].pk)

                stock_avant = produit.quantite_stock
                prix_moyen_avant = produit.prix_achat_moyen
                quantite = ligne["quantite_recue"]

                # Calcul du prix unitaire réel, quel que soit le mode de
                # saisie — c'est CETTE valeur, jamais celle affichée côté
                # app, qui sert de source de vérité pour le CUMP.
                prix_lot_saisi = ligne.get("prix_lot")
                quantite_par_lot = ligne.get("quantite_par_lot")
                if prix_lot_saisi is not None and quantite_par_lot is not None:
                    prix_lot = (prix_lot_saisi / quantite_par_lot).quantize(Decimal("0.01"))
                else:
                    prix_lot = ligne["prix_unitaire_achat"]

                nouveau_stock = stock_avant + quantite
                # Decimal partout ici (jamais float) — cohérent avec la
                # décision du chapitre 4 sur la précision monétaire.
                valeur_totale = (stock_avant * prix_moyen_avant) + (quantite * prix_lot)
                nouveau_prix_moyen = (valeur_totale / nouveau_stock).quantize(Decimal("0.01"))

                LigneAppro.objects.create(
                    appro=appro, produit=produit,
                    quantite_recue=quantite, prix_unitaire_achat=prix_lot,
                    prix_lot=prix_lot_saisi, quantite_par_lot=quantite_par_lot,
                )

                # Ici on assigne les valeurs déjà calculées en Python,
                # pas via F() — contrairement à la vente. Différence
                # volontaire : le calcul du CUMP a BESOIN de connaître
                # la valeur exacte actuelle (pas juste "soustraire X"),
                # et select_for_update() nous garantit qu'aucune autre
                # transaction ne peut avoir changé cette ligne entre
                # notre lecture et notre écriture — le verrou remplace
                # ici le rôle que F() jouait pour la vente.
                produit.quantite_stock = nouveau_stock
                produit.prix_achat_moyen = nouveau_prix_moyen
                champs_modifies = ["quantite_stock", "prix_achat_moyen"]

                prix_vente_saisi = ligne.get("prix_vente")
                if prix_vente_saisi is not None:
                    produit.prix_vente = prix_vente_saisi
                    champs_modifies.append("prix_vente")

                produit.save(update_fields=champs_modifies)

            return appro


class ApprovisionnementLectureSerializer(serializers.ModelSerializer):
    lignes = LigneApproLectureSerializer(many=True, read_only=True)
    fournisseur_nom = serializers.StringRelatedField(source="fournisseur", read_only=True)

    class Meta:
        model = Approvisionnement
        fields = ["id", "date_reception", "fournisseur", "fournisseur_nom", "numero_facture", "lignes"]


# ============================================================
# Chapitre 14 — Prestation de service (ex: déplumage de poulet)
# Plus simple que la vente : pas de stock, juste service x quantité
# ============================================================

from .models import PrestationService


class PrestationCreationSerializer(serializers.Serializer):
    """
    POST /api/prestations/
    {
      "client": 3,               // optionnel, comme pour la vente
      "mode_paiement": "especes",
      "service": 1,               // id du Service ("Déplumage poulet")
      "quantite": 4                // ex: 4 poulets
    }
    """
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True
    )
    mode_paiement = serializers.ChoiceField(choices=["especes", "wave", "orange_money"])
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.all())
    quantite = serializers.IntegerField(min_value=1, default=1)

    def create(self, validated_data):
        utilisateur = self.context["utilisateur"]
        service = validated_data["service"]
        quantite = validated_data["quantite"]
        montant_total = service.tarif * quantite

        # Moins de risque de concurrence ici (pas de stock partagé à
        # verrouiller), mais on garde transaction.atomic() quand même :
        # si la création de PrestationService échoue pour une raison
        # quelconque après celle de TransactionCaisse, on ne veut pas
        # une transaction "orpheline" sans son sous-type.
        with transaction.atomic():
            transaction_caisse = TransactionCaisse.objects.create(
                montant_total=montant_total,
                mode_paiement=validated_data["mode_paiement"],
                utilisateur=utilisateur,
                client=validated_data.get("client"),
            )
            PrestationService.objects.create(
                transaction=transaction_caisse, service=service, quantite=quantite,
            )
            return transaction_caisse


class PrestationLectureSerializer(serializers.ModelSerializer):
    caissier_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)
    client_nom = serializers.StringRelatedField(source="client", read_only=True)
    client_telephone = serializers.SerializerMethodField()
    client_adresse = serializers.SerializerMethodField()
    service_nom = serializers.StringRelatedField(source="prestationservice.service", read_only=True)
    quantite = serializers.IntegerField(source="prestationservice.quantite", read_only=True)

    class Meta:
        model = TransactionCaisse
        fields = [
            "id", "date_heure", "montant_total", "mode_paiement",
            "caissier_nom", "client_nom", "client_telephone", "client_adresse",
            "service_nom", "quantite", "annulee",
        ]

    def get_client_telephone(self, obj):
        return obj.client.telephone if obj.client else None

    def get_client_adresse(self, obj):
        return obj.client.adresse if obj.client else None


# ============================================================
# Chapitre 17 — Gestion des utilisateurs (créer des comptes caissiers)
# Deux objets à créer ensemble : le compte de connexion Django
# (username/password) ET le profil métier Utilisateur (nom/role).
# ============================================================

from django.contrib.auth.models import User
from .permissions import CLES_PERMISSIONS_VALIDES
from django.contrib.auth.hashers import make_password


class UtilisateurCreationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=6)
    nom = serializers.CharField(max_length=100)
    telephone = serializers.CharField(max_length=20)
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())
    # Uniquement pertinent pour un caissier — un admin a déjà tout
    # accès. Validé contre la liste blanche pour éviter qu'une clé
    # inventée ne s'accumule silencieusement en base.
    permissions_supplementaires = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Cet identifiant est déjà utilisé.")
        return value

    def validate_permissions_supplementaires(self, value):
        invalides = [v for v in value if v not in CLES_PERMISSIONS_VALIDES]
        if invalides:
            raise serializers.ValidationError(f"Permission(s) inconnue(s) : {', '.join(invalides)}")
        return value

    def create(self, validated_data):
        with transaction.atomic():
            # make_password() hache le mot de passe (PBKDF2 par défaut
            # avec Django) — jamais stocké en clair, même temporairement.
            user = User.objects.create(
                username=validated_data["username"],
                password=make_password(validated_data["password"]),
            )
            utilisateur = Utilisateur.objects.create(
                compte=user,
                nom=validated_data["nom"],
                telephone=validated_data["telephone"],
                role=validated_data["role"],
                permissions_supplementaires=validated_data.get("permissions_supplementaires", []),
            )
        return utilisateur


class ModifierPermissionsSerializer(serializers.Serializer):
    """Modifie UNIQUEMENT les permissions d'un compte existant (pas son mot de passe/nom)."""
    permissions_supplementaires = serializers.ListField(child=serializers.CharField())

    def validate_permissions_supplementaires(self, value):
        invalides = [v for v in value if v not in CLES_PERMISSIONS_VALIDES]
        if invalides:
            raise serializers.ValidationError(f"Permission(s) inconnue(s) : {', '.join(invalides)}")
        return value


class MonProfilSerializer(serializers.Serializer):
    """
    Auto-modification du profil connecté. Le nom/téléphone ne sont
    modifiables QUE par un admin (appliqué côté vue, pas ici) ; le mot
    de passe est ouvert aux deux rôles.
    """
    nom = serializers.CharField(max_length=100, required=False)
    telephone = serializers.CharField(max_length=20, required=False)
    nouveau_mot_de_passe = serializers.CharField(write_only=True, required=False, min_length=6)


# ============================================================
# Ajustement de stock — correction manuelle et motivée, distincte
# d'une vente (sort du stock) ou d'une réception (entre en stock
# ET affecte le CUMP). Un ajustement ne touche JAMAIS le CUMP.
# ============================================================

from .models import AjustementStock, MotifAjustement, RemboursementCredit


class AjustementStockCreationSerializer(serializers.Serializer):
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.filter(actif=True))
    # DecimalField, pas IntegerField : une correction sur un produit au
    # poids peut être fractionnaire (-0.250 kg de casse constatée).
    delta = serializers.DecimalField(max_digits=10, decimal_places=3)
    motif = serializers.ChoiceField(choices=MotifAjustement.choices)
    commentaire = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("Le delta ne peut pas être nul — indique une vraie correction.")
        return value

    def create(self, validated_data):
        utilisateur = self.context["utilisateur"]
        with transaction.atomic():
            produit = Produit.objects.select_for_update().get(pk=validated_data["produit"].pk)
            nouveau_stock = produit.quantite_stock + validated_data["delta"]
            if nouveau_stock < 0:
                raise serializers.ValidationError(
                    f"Impossible : stock actuel {produit.quantite_stock}, correction demandée {validated_data['delta']:+}."
                )
            ajustement = AjustementStock.objects.create(
                produit=produit, delta=validated_data["delta"], motif=validated_data["motif"],
                commentaire=validated_data.get("commentaire", ""), utilisateur=utilisateur,
            )
            # Affectation directe (pas F()) : select_for_update() a déjà
            # verrouillé la ligne, on connaît la valeur exacte à jour.
            produit.quantite_stock = nouveau_stock
            produit.save(update_fields=["quantite_stock"])
            return ajustement


class AjustementStockLectureSerializer(serializers.ModelSerializer):
    produit_nom = serializers.StringRelatedField(source="produit", read_only=True)
    produit_unite_vente = serializers.CharField(source="produit.unite_vente", read_only=True)
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = AjustementStock
        fields = ["id", "produit", "produit_nom", "produit_unite_vente", "delta", "motif", "commentaire", "utilisateur_nom", "date_heure"]


# ============================================================
# Remboursement de crédit — réduit la dette d'un client
# (Client.solde_credit), avec trace de qui/quand/combien.
# ============================================================

class RemboursementCreditSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    montant = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)

    def validate(self, data):
        if data["montant"] > data["client"].solde_credit:
            raise serializers.ValidationError(
                f"Le montant dépasse la dette du client ({data['client'].solde_credit} F)."
            )
        return data

    def create(self, validated_data):
        utilisateur = self.context["utilisateur"]
        client = validated_data["client"]
        with transaction.atomic():
            RemboursementCredit.objects.create(
                client=client, montant=validated_data["montant"], utilisateur=utilisateur
            )
            client.solde_credit = F("solde_credit") - validated_data["montant"]
            client.save(update_fields=["solde_credit"])
        client.refresh_from_db()
        return client