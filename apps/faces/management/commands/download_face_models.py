"""
Downloads the InsightFace "buffalo_l" pack and keeps the two models face
attendance uses (about 180 MB) in settings.FACE_MODEL_DIR (default: face_models/).

    python manage.py download_face_models
    python manage.py download_face_models --zip C:\\Downloads\\buffalo_l.zip   # already downloaded

The models are free for non-commercial / academic use only (InsightFace licence).
Writes only local files; it does not touch any database.
"""
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

PACK_URL = 'https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip'
FILES = {  # file in the zip -> expected SHA-256
    'det_10g.onnx': '5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91',
    'w600k_r50.onnx': '4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43',
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = 'Download the face recognition models (InsightFace buffalo_l, about 290 MB once).'

    def add_arguments(self, parser):
        parser.add_argument('--zip', help='Use an already downloaded buffalo_l.zip instead of downloading.')

    def handle(self, *args, **options):
        target = Path(settings.FACE_MODEL_DIR)
        target.mkdir(parents=True, exist_ok=True)
        if all((target / name).is_file() and sha256(target / name) == digest for name, digest in FILES.items()):
            self.stdout.write(self.style.SUCCESS(f'Models already in place: {target}'))
            return

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(options['zip']) if options['zip'] else Path(tmp) / 'buffalo_l.zip'
            if not options['zip']:
                self.stdout.write(f'Downloading {PACK_URL} (about 290 MB)...')
                with urllib.request.urlopen(PACK_URL) as response, open(zip_path, 'wb') as out:
                    shutil.copyfileobj(response, out, 1 << 20)
            if not zip_path.is_file():
                raise CommandError(f'File not found: {zip_path}')

            if not zipfile.is_zipfile(zip_path):
                raise CommandError(f'{zip_path} is not a valid zip file. Download it again.')
            with zipfile.ZipFile(zip_path) as pack:
                names = {Path(n).name: n for n in pack.namelist()}
                for name, digest in FILES.items():
                    if name not in names:
                        raise CommandError(f'{name} is not in {zip_path}. Is it the buffalo_l pack?')
                    extracted = Path(tmp) / name
                    with pack.open(names[name]) as src, open(extracted, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    if sha256(extracted) != digest:
                        raise CommandError(f'{name} does not match the expected checksum. Download it again.')
                    shutil.move(str(extracted), target / name)
                    self.stdout.write(f'  saved {target / name}')

        self.stdout.write(self.style.SUCCESS('Face models ready.'))
