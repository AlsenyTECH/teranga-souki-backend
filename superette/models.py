# fichier: superette/models.py
# Fichier déjà existant (créé par startapp) — on REMPLACE tout son contenu

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator


class Role(models.Model):
    # Rappel du MLD : libelle est la clé primaire (pas d'id auto ici,
    # car "caissier"/"admin" est déjà un identifiant naturel et stable)
    libelle = models.CharField(max_length=30, primary_key=True)

    def __str__(self):
        return self.libelle


class Utilisateur(models.Model):
    # NOUVEAU : lien 1-1 vers le modèle User natif de Django, qui gère
    # réellement la connexion (identifiant, mot de passe hashé, token).
    # settings.AUTH_USER_MODEL = le modèle User actif du projet — on
    # écrit ça plutôt que "User" en dur, c'est la convention Django.
    # on_delete=CASCADE ici : si le compte de connexion est supprimé,
    # le profil métier associé n'a plus de sens, il part avec.
    compte = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=20, unique=True)
    # mot_de_passe_hash est SUPPRIMÉ : le hash du mot de passe vit
    # maintenant dans la table auth_user de Django, gérée par son
    # système d'authentification éprouvé — on ne réinvente pas ça.
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    # Permissions accordées EN PLUS du rôle de base — n'a de sens que
    # pour un caissier (un admin a déjà tout). Liste de clés parmi :
    # catalogue, fournisseurs, approvisionnement, clients, depenses,
    # rapports. Ignoré/inutile pour un compte admin.
    permissions_supplementaires = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.nom} ({self.role_id})"


class Categorie(models.Model):
    libelle = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name_plural = "catégories"

    def __str__(self):
        return self.libelle


class UniteVente(models.TextChoices):
    UNITE = "unite", "Unité"
    KG = "kg", "Kilogramme"


class Produit(models.Model):
    nom = models.CharField(max_length=150)
    code_barre = models.CharField(max_length=50, unique=True, blank=True, null=True)
    # DecimalField, jamais FloatField, pour l'argent (rappel chapitre 4) :
    # max_digits=10, decimal_places=2 -> équivalent exact de DECIMAL(10,2)
    prix_achat_moyen = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(0)]
    )
    # Prix "par unité" OU "par kg" selon unite_vente — même champ, le
    # sens change juste selon le contexte du produit.
    prix_vente = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    # NOUVEAU : vente en gros — les deux champs sont nuls ensemble
    # (produit vendu uniquement au détail) ou renseignés ensemble
    # (à partir de seuil_gros unités/kg, prix_vente_gros s'applique
    # automatiquement à la caisse). Configuré une fois ici plutôt que
    # laissé à l'appréciation du caissier au moment de la vente.
    prix_vente_gros = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)]
    )
    seuil_gros = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0.001)]
    )
    # NOUVEAU : distingue les produits vendus à l'unité (Coca, savon...)
    # de ceux vendus au poids (pomme de terre, blanc de poulet...).
    unite_vente = models.CharField(max_length=10, choices=UniteVente.choices, default=UniteVente.UNITE)
    # DecimalField (3 décimales) plutôt que PositiveIntegerField : un
    # produit à l'unité garde des valeurs entières (5.000), un produit
    # au poids peut être fractionnaire (0.750 kg) — un seul type de
    # champ pour les deux cas, pas deux chemins de code séparés.
    quantite_stock = models.DecimalField(max_digits=10, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    seuil_alerte = models.DecimalField(max_digits=10, decimal_places=3, default=5, validators=[MinValueValidator(0)])
    categorie = models.ForeignKey(Categorie, on_delete=models.PROTECT)
    actif = models.BooleanField(default=True)

    def __str__(self):
        return self.nom

    def stock_bas(self):
        # Méthode "métier" simple, directement utilisable dans les vues/rapports
        return self.quantite_stock <= self.seuil_alerte


class Client(models.Model):
    nom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=20, unique=True, blank=True, null=True)
    # NOUVEAU : nécessaire pour faire figurer une adresse sur une
    # facture de prestation (déplumage) — jusqu'ici seul le nom était
    # exposé sur les documents.
    adresse = models.CharField(max_length=255, blank=True)
    solde_credit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    points_fidelite = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.nom


class Fournisseur(models.Model):
    nom = models.CharField(max_length=100)
    contact = models.CharField(max_length=100, blank=True)
    # NOUVEAU : miroir de Client.solde_credit, côté achats — montant que
    # la superette doit encore à ce fournisseur (réceptions à crédit ou
    # partiellement payées). Jamais modifiable en écriture directe via
    # l'API : évolue uniquement via ApprovisionnementCreationSerializer
    # et PaiementFournisseurSerializer.
    solde_du = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return self.nom


class Service(models.Model):
    libelle = models.CharField(max_length=100)
    tarif = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    def __str__(self):
        return self.libelle


class StatutSession(models.TextChoices):
    OUVERTE = "ouverte", "Ouverte"
    FERMEE = "fermee", "Fermée"


class SessionCaisse(models.Model):
    # Une session = le temps entre l'ouverture et la fermeture de la caisse
    # par un caissier (généralement une journée/un service). Sert à
    # rattacher les ventes/prestations réglées en espèces à un fond de
    # caisse compté physiquement, pour produire un rapport Z à la fermeture.
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT, related_name="sessions_caisse")
    date_ouverture = models.DateTimeField(auto_now_add=True)
    fond_ouverture = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    date_fermeture = models.DateTimeField(null=True, blank=True)
    montant_compte = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    # montant_attendu/ecart sont calculés UNE FOIS à la fermeture puis
    # figés (comme Approvisionnement.montant_total) — jamais recalculés
    # après coup, même si une donnée liée changeait ensuite.
    montant_attendu = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ecart = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    statut = models.CharField(max_length=10, choices=StatutSession.choices, default=StatutSession.OUVERTE)
    commentaire_fermeture = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["utilisateur", "statut"])]

    def __str__(self):
        return f"Session #{self.pk} - {self.utilisateur} ({self.statut})"


class ModePaiement(models.TextChoices):
    # models.TextChoices = équivalent Django de l'ENUM SQL.
    # Chaque ligne : (valeur_stockée_en_base, libelle_affiché)
    ESPECES = "especes", "Espèces"
    WAVE = "wave", "Wave"
    ORANGE_MONEY = "orange_money", "Orange Money"
    # NOUVEAU : une vente à crédit n'encaisse rien tout de suite — le
    # montant s'ajoute à la dette du client (Client.solde_credit), à
    # rembourser plus tard. Exige un client identifié (jamais anonyme,
    # sinon personne à qui réclamer la dette).
    CREDIT = "credit", "Crédit client"
    # NOUVEAU : le total est réparti sur plusieurs lignes de paiement
    # (PaiementVente), potentiellement de modes différents (ex: moitié
    # espèces, moitié à crédit) — jamais utilisé comme mode d'une LIGNE
    # de paiement elle-même, seulement comme mode de la transaction
    # globale quand elle est ainsi scindée.
    MIXTE = "mixte", "Paiement mixte"


class TransactionCaisse(models.Model):
    # Nommée TransactionCaisse (pas "Transaction") pour éviter toute
    # confusion avec les transactions de base de données de Django
    date_heure = models.DateTimeField(auto_now_add=True)
    montant_total = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    mode_paiement = models.CharField(max_length=20, choices=ModePaiement.choices)
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)
    # null=True -> le champ peut être vide en base (vente anonyme)
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True)
    annulee = models.BooleanField(default=False)
    # NOUVEAU : nullable car les transactions déjà enregistrées avant
    # l'introduction des sessions n'en ont pas. Toute NOUVELLE vente/
    # prestation doit obligatoirement en avoir une (voir
    # VenteCreationSerializer/PrestationCreationSerializer).
    session_caisse = models.ForeignKey(
        SessionCaisse, on_delete=models.PROTECT, null=True, blank=True, related_name="transactions"
    )

    class Meta:
        indexes = [models.Index(fields=["date_heure"])]  # traduit notre INDEX idx_transaction_date

    def __str__(self):
        return f"Transaction #{self.pk} - {self.montant_total} F"


class VenteProduits(models.Model):
    # OneToOneField = relation 1-1. C'est ici que se traduit la
    # spécialisation TRANSACTION -> VENTE_PRODUITS du MCD : la clé
    # primaire de cette table EST la clé étrangère vers sa transaction.
    transaction = models.OneToOneField(
        TransactionCaisse, on_delete=models.CASCADE, primary_key=True
    )
    # NOUVEAU : réduction accordée sur cette vente, en montant (F).
    # Gardée ici (pas sur TransactionCaisse) car propre à la vente de
    # produits — TransactionCaisse.montant_total reste le montant déjà
    # remisé, réellement encaissé (contrat inchangé pour les points
    # fidélité et le crédit, qui continuent de lire montant_total).
    reduction_montant = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(0)]
    )

    def __str__(self):
        return f"Vente #{self.transaction_id}"


class PaiementVente(models.Model):
    # Détail d'un paiement mixte : une vente à mode_paiement="mixte" a
    # plusieurs lignes ici dont la somme égale son montant_total. Une
    # vente en mode simple (especes/wave/orange_money/credit) n'en a
    # aucune — tout reste lisible directement sur TransactionCaisse.
    vente = models.ForeignKey(TransactionCaisse, on_delete=models.CASCADE, related_name="paiements")
    # Jamais "mixte" ici — uniquement les modes "simples" de ModePaiement.
    mode_paiement = models.CharField(max_length=20, choices=ModePaiement.choices)
    montant = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])

    def __str__(self):
        return f"{self.montant} F ({self.mode_paiement}) sur vente #{self.vente_id}"


class PrestationService(models.Model):
    transaction = models.OneToOneField(
        TransactionCaisse, on_delete=models.CASCADE, primary_key=True
    )
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    quantite = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"Prestation #{self.transaction_id} - {self.service}"


class LigneVente(models.Model):
    quantite = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(0.001)])
    prix_unitaire_vente = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    # related_name : permet d'écrire vente.lignes.all() plus tard,
    # au lieu de vente.lignevente_set.all() (nom par défaut, peu lisible)
    vente = models.ForeignKey(VenteProduits, on_delete=models.CASCADE, related_name="lignes")
    produit = models.ForeignKey(Produit, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.quantite} x {self.produit}"


# ============================================================
# Retour produit / remboursement partiel — retourne tout ou partie
# d'une ligne de vente déjà enregistrée. Ne modifie JAMAIS
# TransactionCaisse.montant_total (intégrité historique) : le montant
# "net" se calcule à la lecture (montant_total - Σ retours).
# ============================================================

class RetourVente(models.Model):
    vente = models.ForeignKey(TransactionCaisse, on_delete=models.PROTECT, related_name="retours")
    # Toujours la session OUVERTE de la personne qui fait le retour —
    # jamais celle de la vente d'origine : l'argent sort de la caisse
    # d'aujourd'hui, pas de celle du jour de la vente. Non-nullable :
    # contrairement à TransactionCaisse.session_caisse, ce modèle est
    # nouveau et n'a aucune ligne historique à tolérer sans session.
    session_caisse = models.ForeignKey(SessionCaisse, on_delete=models.PROTECT, related_name="retours")
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)
    date_heure = models.DateTimeField(auto_now_add=True)
    montant_total = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    # true si ce retour doit être décompté des espèces attendues dans le
    # rapport Z de la session — vrai pour une vente en espèces, faux
    # pour Wave/Orange/crédit (le remboursement se fait alors ailleurs
    # que dans le tiroir-caisse physique).
    affecte_caisse = models.BooleanField(default=False)
    commentaire = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Retour #{self.pk} sur vente #{self.vente_id} - {self.montant_total} F"


class LigneRetour(models.Model):
    retour = models.ForeignKey(RetourVente, on_delete=models.CASCADE, related_name="lignes")
    ligne_vente = models.ForeignKey(LigneVente, on_delete=models.PROTECT, related_name="retours")
    quantite = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(0.001)])

    def __str__(self):
        return f"{self.quantite} x {self.ligne_vente.produit} (retour)"


class ModePaiementAppro(models.TextChoices):
    # Distinct de ModePaiement (especes/wave/orange_money/credit) : ce
    # sont deux domaines différents — un client paie AVEC un moyen, un
    # fournisseur est payé SELON une modalité (tout/partiel/rien).
    COMPTANT = "comptant", "Comptant"
    TRANCHE = "tranche", "Tranche (paiement partiel)"
    CREDIT = "credit", "Crédit fournisseur"


class Approvisionnement(models.Model):
    date_reception = models.DateTimeField(auto_now_add=True)
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.PROTECT)
    # Numéro de la facture papier/PDF fournie par le fournisseur — pas
    # généré par le système, c'est une référence externe qu'on note
    # pour pouvoir rapprocher notre enregistrement du document réel en
    # cas de contrôle ou de litige. Optionnel : certains petits
    # fournisseurs informels n'émettent pas toujours de facture numérotée.
    numero_facture = models.CharField(max_length=50, blank=True)
    # NOUVEAU : jusqu'ici jamais stocké, uniquement recalculé à la volée
    # côté Flutter à partir des lignes — indispensable maintenant pour
    # calculer montant_du = montant_total - montant_paye de façon stable.
    montant_total = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    mode_paiement = models.CharField(
        max_length=20, choices=ModePaiementAppro.choices, default=ModePaiementAppro.COMPTANT
    )
    montant_paye = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )

    def __str__(self):
        return f"Appro #{self.pk} - {self.fournisseur}"


class LigneAppro(models.Model):
    quantite_recue = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(0.001)])
    prix_unitaire_achat = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    # NOUVEAU : traçabilité de l'achat par lot (carton, sac...).
    # Tous les deux nuls -> achat à l'unité classique (comportement
    # inchangé). Tous les deux renseignés -> on garde la trace du lot
    # ACHETÉ EN PLUS du prix unitaire (qui, lui, est toujours calculé
    # et stocké, jamais laissé vide) — comme ça les rapports/CUMP
    # n'ont jamais besoin de savoir si l'achat était en lot ou pas.
    # Même mécanique pour un produit au poids : "1 sac de 25 kg" est
    # un lot de 25 (kg) au lieu d'un carton de N unités — quantite_par_lot
    # devient alors décimal (kg), pas seulement entier (unités).
    prix_lot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    quantite_par_lot = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0.001)])
    appro = models.ForeignKey(Approvisionnement, on_delete=models.CASCADE, related_name="lignes")
    produit = models.ForeignKey(Produit, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.quantite_recue} x {self.produit}"


class TypeRecurrence(models.TextChoices):
    PONCTUELLE = "ponctuelle", "Ponctuelle"
    JOURNALIERE = "journaliere", "Journalière"
    HEBDOMADAIRE = "hebdomadaire", "Hebdomadaire"
    MENSUELLE = "mensuelle", "Mensuelle"


class Depense(models.Model):
    montant = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    categorie = models.CharField(max_length=60)
    description = models.CharField(max_length=255, blank=True)
    date_depense = models.DateField()
    type_recurrence = models.CharField(
        max_length=20, choices=TypeRecurrence.choices, default=TypeRecurrence.PONCTUELLE
    )
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)

    class Meta:
        indexes = [models.Index(fields=["date_depense"])]

    def __str__(self):
        return f"{self.categorie} - {self.montant} F"


# ============================================================
# Ajustement de stock — correction manuelle et TRACÉE d'une
# quantité en stock (perte, casse, vol, comptage physique...).
# Ne touche JAMAIS prix_achat_moyen : contrairement à une
# réception, un ajustement n'a pas de coût d'achat associé.
# ============================================================

class MotifAjustement(models.TextChoices):
    PERTE = "perte", "Perte"
    CASSE = "casse", "Casse"
    VOL = "vol", "Vol"
    COMPTAGE = "comptage", "Comptage physique"
    ERREUR = "erreur", "Erreur de saisie"
    AUTRE = "autre", "Autre"


class AjustementStock(models.Model):
    produit = models.ForeignKey(Produit, on_delete=models.PROTECT)
    # delta signé : +5 (on a retrouvé du stock) ou -3 (perte constatée).
    # Decimal, pas Integer : un produit au poids se corrige aussi en kg
    # fractionnaires (ex: -0.250 kg de casse).
    delta = models.DecimalField(max_digits=10, decimal_places=3)
    motif = models.CharField(max_length=20, choices=MotifAjustement.choices)
    commentaire = models.CharField(max_length=255, blank=True)
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)
    date_heure = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.produit} {self.delta:+} ({self.motif})"


# ============================================================
# Remboursement de crédit — quand un client rembourse tout ou
# partie de sa dette (Client.solde_credit).
# ============================================================

class RemboursementCredit(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    montant = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)
    date_heure = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client} rembourse {self.montant} F"


# ============================================================
# Paiement fournisseur — miroir de RemboursementCredit côté achats :
# quand la superette règle tout ou partie de ce qu'elle doit à un
# fournisseur (Fournisseur.solde_du), typiquement après une réception
# reçue en tranche ou à crédit.
# ============================================================

class PaiementFournisseur(models.Model):
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.PROTECT)
    montant = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)
    date_heure = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Paiement à {self.fournisseur} de {self.montant} F"


# ============================================================
# Archivage de créance — un admin peut "clore" le cycle de dette
# courant d'un client/fournisseur UNE FOIS SON SOLDE À ZÉRO, pour
# repartir sur une liste propre (l'historique détaillé du cycle clos
# reste consultable, juste sorti de la vue "en cours"). Ce n'est PAS
# une remise à zéro du solde (déjà à zéro par construction — la vue
# empêche d'archiver sinon) : juste une photo horodatée + une borne de
# date, utilisée pour partitionner ventes/réceptions passées entre
# "avant" (archivé) et "après" (cycle courant).
# ============================================================

class ArchiveCreanceClient(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="archives_creance")
    date_archivage = models.DateTimeField(auto_now_add=True)
    total_mis_a_credit = models.DecimalField(max_digits=10, decimal_places=2)
    total_rembourse = models.DecimalField(max_digits=10, decimal_places=2)
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-date_archivage"]

    def __str__(self):
        return f"Créance {self.client} archivée le {self.date_archivage:%Y-%m-%d}"


class ArchiveCreanceFournisseur(models.Model):
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.CASCADE, related_name="archives_creance")
    date_archivage = models.DateTimeField(auto_now_add=True)
    total_recu_a_credit = models.DecimalField(max_digits=10, decimal_places=2)
    total_paye = models.DecimalField(max_digits=10, decimal_places=2)
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-date_archivage"]

    def __str__(self):
        return f"Créance {self.fournisseur} archivée le {self.date_archivage:%Y-%m-%d}"