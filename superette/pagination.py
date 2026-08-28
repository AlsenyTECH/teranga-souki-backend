# fichier: superette/pagination.py
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    Pagination standardisée avec support de taille de page personnalisable.
    S'active lorsque 'page' ou 'page_size' est spécifié dans la requête.
    Permet une compatibilité ascendante totale avec les écrans consommant des listes complètes.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200

    def paginate_queryset(self, queryset, request, view=None):
        # Désactivation explicite si demandé
        if request.query_params.get('sans_pagination') == '1' or request.query_params.get('tous') == '1':
            return None
        # Rétrocompatibilité : si aucune pagination demandée explicitement, renvoie la liste complète
        if 'page' not in request.query_params and 'page_size' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view=view)
