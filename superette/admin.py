# fichier: superette/admin.py
# Fichier déjà existant (créé par startapp) — on REMPLACE son contenu

from django.contrib import admin
from .models import (
    Role, Utilisateur, Categorie, Produit, Client, Fournisseur,
    Service, TransactionCaisse, VenteProduits, PrestationService,
    LigneVente, Approvisionnement, LigneAppro, Depense,
)

# admin.site.register() = "expose ce modèle dans l'interface /admin"
admin.site.register(Role)
admin.site.register(Utilisateur)
admin.site.register(Categorie)
admin.site.register(Produit)
admin.site.register(Client)
admin.site.register(Fournisseur)
admin.site.register(Service)
admin.site.register(TransactionCaisse)
admin.site.register(VenteProduits)
admin.site.register(PrestationService)
admin.site.register(LigneVente)
admin.site.register(Approvisionnement)
admin.site.register(LigneAppro)
admin.site.register(Depense)