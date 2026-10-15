# fichier: superette/management/commands/sauvegarder.py
#
# Sauvegarde complète de la base (JSON compressé) dans un dossier, en ne
# gardant que les N plus récentes. À planifier chaque jour sur
# PythonAnywhere (onglet « Tasks ») :
#   cd ~/teranga-souki-backend && python manage.py sauvegarder
#
# Restauration sur une base vide (après « migrate ») :
#   python manage.py loaddata sauvegardes/teranga-souki-AAAAMMJJ-HHMM.json.gz

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from superette.views import contenu_sauvegarde


class Command(BaseCommand):
    help = "Sauvegarde la base dans un fichier .json.gz et supprime les plus anciennes."

    def add_arguments(self, parser):
        parser.add_argument("--dossier", default=str(Path(settings.BASE_DIR) / "sauvegardes"))
        parser.add_argument("--garder", type=int, default=14, help="Nombre de sauvegardes conservées (défaut 14).")

    def handle(self, *args, **options):
        dossier = Path(options["dossier"])
        dossier.mkdir(parents=True, exist_ok=True)
        fichier = dossier / f"teranga-souki-{timezone.localtime():%Y%m%d-%H%M}.json.gz"
        fichier.write_bytes(contenu_sauvegarde())
        anciennes = sorted(dossier.glob("teranga-souki-*.json.gz"))[:-max(1, options["garder"])]
        for f in anciennes:
            f.unlink()
        self.stdout.write(self.style.SUCCESS(
            f"Sauvegarde : {fichier} ({fichier.stat().st_size // 1024} Ko). "
            f"{len(anciennes)} ancienne(s) supprimée(s)."
        ))
