"""
Checks that face recognition works on this computer: loads the models and
counts the faces in a photo. Reads only the photo; writes nothing.

    python manage.py check_face_engine path\\to\\class-photo.jpg --settings=config.settings.test
"""
import time

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from apps.faces.engines import FaceEngineUnavailable, get_engine
from apps.faces.services import FaceError, read_image


class Command(BaseCommand):
    help = 'Load the face models and count the faces in a photo.'

    def add_arguments(self, parser):
        parser.add_argument('photo', help='Path to a JPG or PNG photo.')

    def handle(self, *args, **options):
        try:
            started = time.perf_counter()
            engine = get_engine()
            self.stdout.write(f'Engine {engine.name} loaded in {time.perf_counter() - started:.1f}s')
            with open(options['photo'], 'rb') as f:
                image = read_image(ContentFile(f.read()))
            started = time.perf_counter()
            faces = engine.analyze(image, max_side=settings.FACE_CLASS_PHOTO_MAX_SIDE)
        except (FaceEngineUnavailable, FaceError, OSError) as e:
            raise CommandError(str(getattr(e, 'message', e)))

        self.stdout.write(
            f'{len(faces)} face(s) found in {time.perf_counter() - started:.1f}s '
            f'(photo {image.shape[1]}x{image.shape[0]})'
        )
        for face in faces:
            x1, y1, x2, y2 = face.box
            self.stdout.write(f'  at ({x1:.0f}, {y1:.0f}) size {x2 - x1:.0f}px, confidence {face.score:.2f}')
        self.stdout.write(self.style.SUCCESS('Face recognition works.'))
