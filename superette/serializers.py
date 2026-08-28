# fichier: superette/serializers.py
# Fichier À CRÉER (n'existe pas encore dans superette/) — n'existe pas par défaut

from decimal import Decimal
from rest_framework import serializers
from .models import (
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur, Service, Depense,
)


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["libelle"]


class UtilisateurSerializer(serializers.ModelSerializer):
    # Lecture seule : l'identifiant de connexion vit sur compte.username
    # (le User Django), pas sur Utilisateur — exposé ici pour que l'admin
    # puisse le voir avant de le modifier (UtilisateurEditionSerializer).
    username = serializers.CharField(source="compte.username", read_only=True)

    class Meta:
        model = Utilisateur
        # Liste EXPLICITE des champs exposés — jamais fields = "__all__"
        # sur un modèle contenant un mot de passe : mot_de_passe_hash
        # ne doit JAMAIS apparaître dans une réponse JSON.
        fields = ["id", "nom", "telephone", "username", "role", "actif", "date_creation", "permissions_supplementaires"]
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
            "prix_vente_gros", "seuil_gros",
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

    def validate(self, data):
        # prix_vente_gros et seuil_gros vont toujours ensemble : soit le
        # produit n'a pas de palier de gros, soit il a les deux valeurs.
        prix_gros = data.get("prix_vente_gros", getattr(self.instance, "prix_vente_gros", None))
        seuil_gros = data.get("seuil_gros", getattr(self.instance, "seuil_gros", None))
        if (prix_gros is None) != (seuil_gros is None):
            raise serializers.ValidationError(
                "Le prix de gros et le seuil de quantité doivent être renseignés ensemble."
            )
        return data


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
        fields = ["id", "nom", "contact", "solde_du"]
        # Même règle que Client.solde_credit : jamais modifiable en
        # écriture directe, uniquement via les réceptions et paiements.
        read_only_fields = ["solde_du"]


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
from django.db.models import F, Sum
from .models import TransactionCaisse, VenteProduits, LigneVente, SessionCaisse, StatutSession, PaiementVente


class LigneVenteEcritureSerializer(serializers.Serializer):
    # serializers.Serializer (pas ModelSerializer) : on ne veut PAS
    # créer directement un objet LigneVente ici, juste valider la
    # forme des données reçues. La création réelle se fait "à la main"
    # dans VenteCreationSerializer.create(), où on a besoin de vérifier
    # le stock avant de créer quoi que ce soit.
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.filter(actif=True))
    quantite = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    prix_unitaire_vente = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False
    )


class LigneVenteLectureSerializer(serializers.ModelSerializer):
    produit_nom = serializers.StringRelatedField(source="produit", read_only=True)
    # Propagé jusqu'au reçu/à l'historique : sans ça, le frontend ne
    # peut pas savoir s'il doit afficher "2" ou "0.750 kg".
    produit_unite_vente = serializers.CharField(source="produit.unite_vente", read_only=True)
    quantite_retournee = serializers.SerializerMethodField()
    quantite_restante = serializers.SerializerMethodField()

    class Meta:
        model = LigneVente
        fields = [
            "id", "produit", "produit_nom", "produit_unite_vente", "quantite", "prix_unitaire_vente",
            "quantite_retournee", "quantite_restante",
        ]

    def get_quantite_retournee(self, obj):
        return obj.retours.aggregate(total=Sum("quantite"))["total"] or Decimal("0")

    def get_quantite_restante(self, obj):
        return obj.quantite - self.get_quantite_retournee(obj)


class PaiementVenteEcritureSerializer(serializers.Serializer):
    # Jamais "mixte" au niveau d'une ligne — seulement les modes simples.
    mode_paiement = serializers.ChoiceField(choices=["especes", "wave", "orange_money", "credit"])
    montant = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


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

    Paiement mixte (le total est réparti sur plusieurs lignes) :
    {
      "mode_paiement": "mixte",
      "client": 7,
      "paiements": [
        {"mode_paiement": "especes", "montant": "5000.00"},
        {"mode_paiement": "credit", "montant": "3000.00"}
      ],
      "lignes": [...]
    }
    """
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True
    )
    mode_paiement = serializers.ChoiceField(choices=["especes", "wave", "orange_money", "credit", "mixte"])
    lignes = LigneVenteEcritureSerializer(many=True)
    # Réduction sur le total de la vente (montant, pas pourcentage — le
    # frontend calcule déjà un éventuel pourcentage en montant avant
    # l'envoi, pour que le serveur n'ait qu'une seule règle à valider).
    reduction_montant = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False
    )
    paiements = PaiementVenteEcritureSerializer(many=True, required=False)

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError("Une vente doit contenir au moins une ligne.")
        return value

    def validate(self, data):
        # Une vente à crédit (simple ou la part crédit d'un paiement
        # mixte) doit pouvoir être réclamée à quelqu'un — impossible de
        # la laisser anonyme, contrairement aux autres modes où
        # l'anonymat est un choix légitime.
        if data.get("mode_paiement") == "credit" and not data.get("client"):
            raise serializers.ValidationError(
                "Une vente à crédit doit être attribuée à un client identifié (pas anonyme)."
            )

        paiements = data.get("paiements")
        if data.get("mode_paiement") == "mixte":
            if not paiements or len(paiements) < 2:
                raise serializers.ValidationError(
                    "Un paiement mixte doit contenir au moins deux lignes — utilise directement "
                    "le mode correspondant pour un paiement simple."
                )
            lignes_credit = [p for p in paiements if p["mode_paiement"] == "credit"]
            if len(lignes_credit) > 1:
                raise serializers.ValidationError("Un paiement mixte ne peut avoir qu'une seule part à crédit.")
            if lignes_credit and not data.get("client"):
                raise serializers.ValidationError(
                    "La part à crédit d'un paiement mixte doit être attribuée à un client identifié."
                )
        elif paiements:
            raise serializers.ValidationError(
                "\"paiements\" ne s'utilise qu'avec mode_paiement=\"mixte\"."
            )

        reduction = data.get("reduction_montant") or Decimal("0")
        sous_total = sum(
            (ligne.get("prix_unitaire_vente") or ligne["produit"].prix_vente) * ligne["quantite"]
            for ligne in data.get("lignes", [])
        )
        if reduction > 0 and reduction > sous_total:
            raise serializers.ValidationError("La réduction dépasse le sous-total de la vente.")

        if paiements:
            total_paiements = sum(p["montant"] for p in paiements)
            total_attendu = sous_total - reduction
            if abs(total_paiements - total_attendu) > Decimal("0.01"):
                raise serializers.ValidationError(
                    f"La somme des paiements ({total_paiements} F) ne correspond pas "
                    f"au total de la vente ({total_attendu} F)."
                )
        return data

    def create(self, validated_data):
        lignes_data = validated_data.pop("lignes")
        utilisateur = self.context["utilisateur"]  # injecté depuis la vue

        # transaction.atomic() : tout le bloc réussit ensemble, ou
        # tout est annulé (rollback) au moindre problème — y compris
        # si une exception Python est levée n'importe où dans le bloc.
        with transaction.atomic():
            # Toute vente doit être rattachée à une session de caisse
            # ouverte — c'est ce qui permet de produire un rapport Z
            # fiable à la fermeture (voir SessionCaisse). Vérifié ici,
            # au tout début du bloc atomic, avant tout verrou produit.
            session = SessionCaisse.objects.filter(
                utilisateur=utilisateur, statut=StatutSession.OUVERTE
            ).first()
            if session is None:
                raise serializers.ValidationError(
                    "Aucune session de caisse ouverte — ouvre la caisse avant d'enregistrer une vente."
                )

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

            # La réduction est soustraite AVANT de créer la transaction :
            # montant_total doit toujours refléter ce qui est réellement
            # encaissé, puisque les points fidélité et l'incrément du
            # solde crédit (plus bas) se basent dessus.
            reduction = validated_data.get("reduction_montant") or Decimal("0")
            montant_total -= reduction

            transaction_caisse = TransactionCaisse.objects.create(
                montant_total=montant_total,
                mode_paiement=validated_data["mode_paiement"],
                utilisateur=utilisateur,
                client=validated_data.get("client"),
                session_caisse=session,
            )
            vente = VenteProduits.objects.create(
                transaction=transaction_caisse, reduction_montant=reduction
            )

            # Paiement mixte : une ligne PaiementVente par mode déclaré.
            # validate() a déjà vérifié que leur somme == montant_total.
            paiements_data = validated_data.get("paiements") or []
            montant_credit = Decimal("0")
            for paiement in paiements_data:
                PaiementVente.objects.create(
                    vente=transaction_caisse,
                    mode_paiement=paiement["mode_paiement"],
                    montant=paiement["montant"],
                )
                if paiement["mode_paiement"] == "credit":
                    montant_credit = paiement["montant"]

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
                # RemboursementCreditSerializer plus bas). En paiement
                # mixte, seule LA PART à crédit (pas le total) s'ajoute.
                if validated_data["mode_paiement"] == "credit":
                    client.solde_credit = F("solde_credit") + montant_total
                    client.save(update_fields=["solde_credit"])
                elif validated_data["mode_paiement"] == "mixte" and montant_credit > 0:
                    client.solde_credit = F("solde_credit") + montant_credit
                    client.save(update_fields=["solde_credit"])

            return transaction_caisse


class PaiementVenteLectureSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaiementVente
        fields = ["id", "mode_paiement", "montant"]


class TransactionCaisseLectureSerializer(serializers.ModelSerializer):
    lignes = LigneVenteLectureSerializer(source="venteproduits.lignes", many=True, read_only=True)
    caissier_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)
    client_nom = serializers.StringRelatedField(source="client", read_only=True)
    # SerializerMethodField plutôt qu'un simple source="client.telephone" :
    # évite une AttributeError quand la vente est anonyme (client=None).
    client_telephone = serializers.SerializerMethodField()
    client_adresse = serializers.SerializerMethodField()
    reduction_montant = serializers.SerializerMethodField()
    sous_total = serializers.SerializerMethodField()
    montant_retourne = serializers.SerializerMethodField()
    montant_net = serializers.SerializerMethodField()
    # Liste vide pour toute vente non-mixte — champ additif.
    paiements = PaiementVenteLectureSerializer(many=True, read_only=True)

    class Meta:
        model = TransactionCaisse
        fields = [
            "id", "date_heure", "montant_total", "mode_paiement",
            "caissier_nom", "client_nom", "client_telephone", "client_adresse", "annulee", "lignes",
            "reduction_montant", "sous_total", "montant_retourne", "montant_net", "paiements",
        ]

    def get_client_telephone(self, obj):
        return obj.client.telephone if obj.client else None

    def get_client_adresse(self, obj):
        return obj.client.adresse if obj.client else None

    def get_reduction_montant(self, obj):
        vente = getattr(obj, "venteproduits", None)
        montant = vente.reduction_montant if vente else Decimal("0")
        return montant.quantize(Decimal("0.01"))

    def get_montant_retourne(self, obj):
        total = obj.retours.aggregate(total=Sum("montant_total"))["total"] or Decimal("0")
        return total.quantize(Decimal("0.01"))

    def get_montant_net(self, obj):
        return (obj.montant_total - self.get_montant_retourne(obj)).quantize(Decimal("0.01"))

    def get_sous_total(self, obj):
        # obj.montant_total peut porter plus de 2 décimales en mémoire
        # (Decimal(2dp) * Decimal(3dp) = 5dp, jamais réarrondi tant que
        # l'objet n'est pas relu depuis la base) — quantize() ici évite
        # de propager ce bruit de précision dans un champ affiché tel quel.
        total = obj.montant_total + self.get_reduction_montant(obj)
        return total.quantize(Decimal("0.01"))


# ============================================================
# Retour produit / remboursement partiel — retourne tout ou partie
# d'une ligne de vente déjà enregistrée. Ne modifie jamais
# TransactionCaisse.montant_total ; montant_net se calcule à la lecture.
# ============================================================

from .models import RetourVente, LigneRetour


class LigneRetourEcritureSerializer(serializers.Serializer):
    ligne_vente = serializers.PrimaryKeyRelatedField(queryset=LigneVente.objects.all())
    quantite = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))


class RetourVenteCreationSerializer(serializers.Serializer):
    """
    POST /api/ventes/<id>/retour/
    {
      "lignes": [{"ligne_vente": 45, "quantite": 1}],
      "rembourser_en_especes": false,
      "commentaire": "Produit périmé"
    }
    Le contexte doit fournir "vente" (la TransactionCaisse déjà verrouillée
    par la vue) et "utilisateur".
    """
    lignes = LigneRetourEcritureSerializer(many=True)
    rembourser_en_especes = serializers.BooleanField(required=False, default=False)
    commentaire = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError("Un retour doit contenir au moins une ligne.")
        return value

    def validate(self, data):
        vente = self.context["vente"]
        if vente.annulee:
            raise serializers.ValidationError("Cette vente est annulée, aucun retour n'est possible.")
        venteproduits = getattr(vente, "venteproduits", None)
        if venteproduits is None:
            raise serializers.ValidationError("Les retours ne s'appliquent qu'aux ventes de produits.")

        for ligne in data["lignes"]:
            if ligne["ligne_vente"].vente_id != venteproduits.pk:
                raise serializers.ValidationError("Une des lignes indiquées n'appartient pas à cette vente.")

        if data.get("rembourser_en_especes"):
            if vente.mode_paiement != "mixte":
                raise serializers.ValidationError(
                    "\"Rembourser en espèces\" ne s'applique qu'à une vente en paiement mixte."
                )
            if not vente.paiements.filter(mode_paiement="especes").exists():
                raise serializers.ValidationError(
                    "Cette vente n'a pas de portion payée en espèces à rembourser en liquide."
                )

        utilisateur = self.context["utilisateur"]
        if not SessionCaisse.objects.filter(utilisateur=utilisateur, statut=StatutSession.OUVERTE).exists():
            raise serializers.ValidationError(
                "Aucune session de caisse ouverte — ouvre la caisse avant d'enregistrer un retour."
            )
        return data

    def create(self, validated_data):
        vente = self.context["vente"]
        utilisateur = self.context["utilisateur"]

        with transaction.atomic():
            session = SessionCaisse.objects.filter(utilisateur=utilisateur, statut=StatutSession.OUVERTE).first()
            if session is None:
                raise serializers.ValidationError(
                    "Aucune session de caisse ouverte — ouvre la caisse avant d'enregistrer un retour."
                )

            montant_total = Decimal("0")
            lignes_pretes = []
            for ligne in validated_data["lignes"]:
                # select_for_update() verrouille la ligne de vente le
                # temps du calcul de la quantité déjà retournue, pour
                # empêcher deux retours concurrents de dépasser la
                # quantité vendue à eux deux.
                ligne_vente = LigneVente.objects.select_for_update().get(pk=ligne["ligne_vente"].pk)
                deja_retournee = ligne_vente.retours.aggregate(total=Sum("quantite"))["total"] or Decimal("0")
                restante = ligne_vente.quantite - deja_retournee
                if ligne["quantite"] > restante:
                    raise serializers.ValidationError(
                        f"Impossible de retourner {ligne['quantite']} de {ligne_vente.produit.nom} "
                        f"— il n'en reste que {restante} à retourner sur cette ligne."
                    )
                montant_ligne = ligne_vente.prix_unitaire_vente * ligne["quantite"]
                montant_total += montant_ligne
                lignes_pretes.append((ligne_vente, ligne["quantite"]))

            if vente.mode_paiement == "especes":
                affecte_caisse = True
            elif vente.mode_paiement == "mixte":
                affecte_caisse = bool(validated_data.get("rembourser_en_especes"))
            else:
                affecte_caisse = False

            retour = RetourVente.objects.create(
                vente=vente,
                session_caisse=session,
                utilisateur=utilisateur,
                montant_total=montant_total,
                affecte_caisse=affecte_caisse,
                commentaire=validated_data.get("commentaire", ""),
            )
            for ligne_vente, quantite in lignes_pretes:
                LigneRetour.objects.create(retour=retour, ligne_vente=ligne_vente, quantite=quantite)
                produit = Produit.objects.select_for_update().get(pk=ligne_vente.produit_id)
                produit.quantite_stock = F("quantite_stock") + quantite
                produit.save(update_fields=["quantite_stock"])

            # Une vente à crédit remboursée en partie réduit d'autant la
            # dette du client — peut devenir négatif si le client avait
            # déjà remboursé une partie de cette même vente (avoir en
            # faveur du client, assumé et affiché comme tel côté fiche client).
            if vente.mode_paiement == "credit" and vente.client_id:
                client = Client.objects.select_for_update().get(pk=vente.client_id)
                client.solde_credit = F("solde_credit") - montant_total
                client.save(update_fields=["solde_credit"])
            elif vente.mode_paiement == "mixte" and vente.client_id:
                # La part à crédit d'une vente mixte n'est pas rattachée à
                # des lignes précises (juste un montant global) — on
                # réduit donc la dette au prorata de la part que ce retour
                # représente sur le total de la vente d'origine.
                ligne_credit = vente.paiements.filter(mode_paiement="credit").first()
                if ligne_credit:
                    montant_a_deduire = (ligne_credit.montant * montant_total / vente.montant_total).quantize(
                        Decimal("0.01")
                    )
                    client = Client.objects.select_for_update().get(pk=vente.client_id)
                    client.solde_credit = F("solde_credit") - montant_a_deduire
                    client.save(update_fields=["solde_credit"])

            return retour


class RetourVenteLectureSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = RetourVente
        fields = [
            "id", "vente", "session_caisse", "utilisateur_nom", "date_heure",
            "montant_total", "affecte_caisse", "commentaire",
        ]


# ============================================================
# Chapitre 13 — Approvisionnement : réception de marchandise
# avec recalcul automatique du CUMP (coût unitaire moyen pondéré)
# ============================================================

from decimal import Decimal
from .models import Approvisionnement, LigneAppro, Fournisseur, ModePaiementAppro


class LigneApproEcritureSerializer(serializers.Serializer):
    produit = serializers.PrimaryKeyRelatedField(queryset=Produit.objects.filter(actif=True))
    quantite_recue = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    prix_unitaire_achat = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False)
    prix_lot = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False)
    quantite_par_lot = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"), required=False)
    prix_vente = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False)

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
    # Modalité de paiement du fournisseur pour cette réception — comptant
    # (tout payé maintenant), tranche (paiement partiel, reste dû) ou
    # crédit (rien payé, tout dû). Miroir du crédit client, mais côté achats.
    mode_paiement = serializers.ChoiceField(
        choices=ModePaiementAppro.choices, default=ModePaiementAppro.COMPTANT
    )
    montant_paye = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False
    )

    def validate_lignes(self, value):
        if not value:
            raise serializers.ValidationError("Un approvisionnement doit contenir au moins une ligne.")
        return value

    def validate(self, data):
        mode = data.get("mode_paiement", ModePaiementAppro.COMPTANT)
        montant_paye = data.get("montant_paye")
        if mode == ModePaiementAppro.CREDIT and montant_paye:
            raise serializers.ValidationError(
                "Une réception à crédit ne peut pas avoir de montant payé."
            )
        if mode == ModePaiementAppro.TRANCHE and montant_paye is None:
            raise serializers.ValidationError(
                "Indique le montant payé maintenant pour un paiement en tranche."
            )
        return data

    def create(self, validated_data):
        lignes_data = validated_data.pop("lignes")
        mode = validated_data.get("mode_paiement", ModePaiementAppro.COMPTANT)

        with transaction.atomic():
            appro = Approvisionnement.objects.create(
                fournisseur=validated_data["fournisseur"],
                numero_facture=validated_data.get("numero_facture", ""),
                mode_paiement=mode,
            )

            montant_total_calcule = Decimal("0")
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

                montant_total_calcule += quantite * prix_lot

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

            if mode == ModePaiementAppro.COMPTANT:
                montant_paye_final = montant_total_calcule
            elif mode == ModePaiementAppro.CREDIT:
                montant_paye_final = Decimal("0")
            else:
                montant_paye_final = validated_data.get("montant_paye") or Decimal("0")
                if montant_paye_final > montant_total_calcule:
                    raise serializers.ValidationError(
                        "Le montant payé dépasse le total de la réception."
                    )

            appro.montant_total = montant_total_calcule
            appro.montant_paye = montant_paye_final
            appro.save(update_fields=["montant_total", "montant_paye"])

            montant_du = montant_total_calcule - montant_paye_final
            if montant_du > 0:
                fournisseur = Fournisseur.objects.select_for_update().get(
                    pk=validated_data["fournisseur"].pk
                )
                fournisseur.solde_du = F("solde_du") + montant_du
                fournisseur.save(update_fields=["solde_du"])

            return appro


class ApprovisionnementLectureSerializer(serializers.ModelSerializer):
    lignes = LigneApproLectureSerializer(many=True, read_only=True)
    fournisseur_nom = serializers.StringRelatedField(source="fournisseur", read_only=True)

    class Meta:
        model = Approvisionnement
        fields = [
            "id", "date_reception", "fournisseur", "fournisseur_nom", "numero_facture", "lignes",
            "montant_total", "mode_paiement", "montant_paye",
        ]


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
            # Même règle que pour une vente : une prestation payée en
            # espèces doit compter dans le rapport Z de la session en cours.
            session = SessionCaisse.objects.filter(
                utilisateur=utilisateur, statut=StatutSession.OUVERTE
            ).first()
            if session is None:
                raise serializers.ValidationError(
                    "Aucune session de caisse ouverte — ouvre la caisse avant d'enregistrer une prestation."
                )

            transaction_caisse = TransactionCaisse.objects.create(
                montant_total=montant_total,
                mode_paiement=validated_data["mode_paiement"],
                utilisateur=utilisateur,
                client=validated_data.get("client"),
                session_caisse=session,
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


class UtilisateurEditionSerializer(serializers.Serializer):
    """
    Édition par un ADMIN d'un AUTRE compte (nom, téléphone, identifiant
    de connexion, mot de passe) — distinct de ModifierPermissionsSerializer
    (permissions uniquement) et de MonProfilSerializer (auto-édition).
    Tous les champs sont optionnels : seuls ceux envoyés sont modifiés.
    """
    nom = serializers.CharField(max_length=100, required=False)
    telephone = serializers.CharField(max_length=20, required=False)
    username = serializers.CharField(max_length=150, required=False)
    nouveau_mot_de_passe = serializers.CharField(write_only=True, required=False, min_length=6)

    def validate_username(self, value):
        # exclude=self.instance : permet de renvoyer le même identifiant
        # sans se le faire refuser comme "déjà utilisé" par soi-même.
        qs = User.objects.filter(username=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.compte_id)
        if qs.exists():
            raise serializers.ValidationError("Cet identifiant est déjà utilisé.")
        return value

    def validate_telephone(self, value):
        qs = Utilisateur.objects.filter(telephone=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ce téléphone est déjà utilisé par un autre compte.")
        return value


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
    """
    POST /api/remboursements-credit/
    {
      "client": 3,
      "montant": "5000.00"
    }
    """
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    montant = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))

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


class RemboursementCreditLectureSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = RemboursementCredit
        fields = ["id", "client", "montant", "utilisateur_nom", "date_heure"]


# ============================================================
# Paiement fournisseur — miroir de RemboursementCredit, côté achats :
# réduit Fournisseur.solde_du quand la superette règle une dette.
# ============================================================

from .models import PaiementFournisseur


class PaiementFournisseurSerializer(serializers.Serializer):
    """
    POST /api/paiements-fournisseur/
    {
      "fournisseur": 2,
      "montant": "5000.00"
    }
    """
    fournisseur = serializers.PrimaryKeyRelatedField(queryset=Fournisseur.objects.all())
    montant = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))

    def validate(self, data):
        if data["montant"] > data["fournisseur"].solde_du:
            raise serializers.ValidationError(
                f"Le montant dépasse la dette envers ce fournisseur ({data['fournisseur'].solde_du} F)."
            )
        return data

    def create(self, validated_data):
        utilisateur = self.context["utilisateur"]
        fournisseur = validated_data["fournisseur"]
        with transaction.atomic():
            PaiementFournisseur.objects.create(
                fournisseur=fournisseur, montant=validated_data["montant"], utilisateur=utilisateur
            )
            fournisseur.solde_du = F("solde_du") - validated_data["montant"]
            fournisseur.save(update_fields=["solde_du"])
        fournisseur.refresh_from_db()
        return fournisseur


class PaiementFournisseurLectureSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = PaiementFournisseur
        fields = ["id", "fournisseur", "montant", "utilisateur_nom", "date_heure"]


# ============================================================
# Session de caisse — ouverture/fermeture, avec rapport Z à la
# fermeture (fond déclaré vs espèces réellement comptées).
# ============================================================

from django.utils import timezone


def calculer_totaux_session(session):
    """
    Calcule les totaux d'une session de caisse — utilisé aussi bien pour
    l'affichage en direct (session encore ouverte, GET .../courante/) que
    pour figer le rapport Z à la fermeture. Ne modifie jamais la session.
    """
    transactions = TransactionCaisse.objects.filter(session_caisse=session, annulee=False)
    ventes_especes = transactions.filter(mode_paiement="especes").aggregate(
        total=Sum("montant_total")
    )["total"] or Decimal("0")
    # Part espèces des ventes en paiement mixte de cette session — une
    # vente mixte n'a pas mode_paiement="especes" (c'est "mixte"), donc
    # invisible à l'agrégat ci-dessus ; sa ligne PaiementVente en espèces
    # doit quand même compter dans la caisse physique.
    ventes_especes_mixte = PaiementVente.objects.filter(
        mode_paiement="especes", vente__session_caisse=session, vente__annulee=False
    ).aggregate(total=Sum("montant"))["total"] or Decimal("0")
    ventes_especes += ventes_especes_mixte
    # Les retours en espèces sortent physiquement de LA CAISSE DU JOUR,
    # donc toujours rattachés à la session actuellement ouverte (voir
    # RetourVenteCreationSerializer) — jamais à la session de la vente
    # d'origine, qui peut être une session différente, déjà fermée.
    retours_especes = RetourVente.objects.filter(session_caisse=session, affecte_caisse=True).aggregate(
        total=Sum("montant_total")
    )["total"] or Decimal("0")
    montant_attendu = session.fond_ouverture + ventes_especes - retours_especes
    return {
        "ventes_especes": ventes_especes,
        "retours_especes": retours_especes,
        "montant_attendu": montant_attendu,
    }


class SessionCaisseOuvertureSerializer(serializers.Serializer):
    """
    POST /api/sessions-caisse/ouvrir/
    { "fond_ouverture": "10000.00" }
    """
    fond_ouverture = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False
    )

    def create(self, validated_data):
        utilisateur = self.context["utilisateur"]
        if SessionCaisse.objects.filter(utilisateur=utilisateur, statut=StatutSession.OUVERTE).exists():
            raise serializers.ValidationError("Tu as déjà une session de caisse ouverte.")
        return SessionCaisse.objects.create(
            utilisateur=utilisateur,
            fond_ouverture=validated_data.get("fond_ouverture") or Decimal("0"),
        )


class SessionCaisseFermetureSerializer(serializers.Serializer):
    """
    POST /api/sessions-caisse/<id>/fermer/
    { "montant_compte": "45000.00", "commentaire": "RAS" }
    """
    montant_compte = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"))
    commentaire = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def update(self, instance, validated_data):
        totaux = calculer_totaux_session(instance)
        instance.montant_attendu = totaux["montant_attendu"]
        instance.montant_compte = validated_data["montant_compte"]
        instance.ecart = validated_data["montant_compte"] - totaux["montant_attendu"]
        instance.commentaire_fermeture = validated_data.get("commentaire", "")
        instance.date_fermeture = timezone.now()
        instance.statut = StatutSession.FERMEE
        instance.save(update_fields=[
            "montant_attendu", "montant_compte", "ecart",
            "commentaire_fermeture", "date_fermeture", "statut",
        ])
        return instance


class SessionCaisseLectureSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = SessionCaisse
        fields = [
            "id", "utilisateur", "utilisateur_nom", "date_ouverture", "fond_ouverture",
            "date_fermeture", "montant_compte", "montant_attendu", "ecart",
            "statut", "commentaire_fermeture",
        ]


# ============================================================
# Archivage de créance — voir le commentaire sur les modèles
# ArchiveCreanceClient/ArchiveCreanceFournisseur (models.py).
# ============================================================

from .models import ArchiveCreanceClient, ArchiveCreanceFournisseur


class ArchiveCreanceClientSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = ArchiveCreanceClient
        fields = ["id", "date_archivage", "total_mis_a_credit", "total_rembourse", "utilisateur_nom"]


class ArchiveCreanceFournisseurSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.StringRelatedField(source="utilisateur", read_only=True)

    class Meta:
        model = ArchiveCreanceFournisseur
        fields = ["id", "date_archivage", "total_recu_a_credit", "total_paye", "utilisateur_nom"]