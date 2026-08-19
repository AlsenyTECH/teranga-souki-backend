# fichier: config/urls.py
# Fichier déjà existant (créé par startproject) — on MODIFIE son contenu

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    # Toutes les URLs de l'app superette seront préfixées par /api/
    # Ex: /api/produits/, /api/connexion/
    path("api/", include("superette.urls")),
]