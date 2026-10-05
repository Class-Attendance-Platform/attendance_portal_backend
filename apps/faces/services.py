"""
Face attendance logic: register a student's face, find students in class
photos, and save the teacher-confirmed result. Class photos are only held in
memory while a request runs; they are never stored.
"""
import base64
import io

import cv2
import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.academic.models import StudentClassroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.faces.engines import get_engine
from apps.faces.models import StudentFace

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 3000          # larger photos are shrunk first (memory and speed)
MAX_IMAGE_PIXELS = 50_000_000  # refuse bigger images before decoding them (memory)
SELFIE_MAX_SIDE = 640
MIN_FACE_PIXELS = 60           # registration: face must be at least this wide
POSE_LABELS = {'STRAIGHT': 'straight', 'LEFT': 'left', 'RIGHT': 'right'}


class FaceError(Exception):
    """A problem the user can fix; the message is shown as it is."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_image(upload, label='photo') -> np.ndarray:
    """Uploaded file -> BGR image, upright (phone rotation applied), at most MAX_IMAGE_SIDE."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise FaceError(f'The {label} is too large (10 MB at most).')
    try:
        image = Image.open(io.BytesIO(upload.read()))  # reads only the header so far
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise FaceError(f'The {label} is too large. Please use a normal camera photo.')
        image.draft('RGB', (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))  # JPEG: decode at a smaller size
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        image = ImageOps.exif_transpose(image).convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise FaceError(f'Could not read the {label}. Please use a JPG or PNG image.')
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def embedding_to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def embedding_from_bytes(data) -> np.ndarray:
    return np.frombuffer(bytes(data), dtype=np.float32)


def crop_to_jpeg(crop: np.ndarray) -> bytes:
    return cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def jpeg_data_url(data) -> str:
    return 'data:image/jpeg;base64,' + base64.b64encode(bytes(data)).decode('ascii')


def _area(face):
    x1, y1, x2, y2 = face.box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


# ── Student registration ──────────────────────────────────────────────────────

def face_status(student) -> dict:
    faces = list(student.faces.order_by('created_at'))
    return {
        'registered': bool(faces),
        'registered_at': faces[0].created_at.isoformat() if faces else None,
        'poses': [f.pose for f in faces],
    }


def register_student_faces(student, uploads: dict) -> dict:
    """
    uploads: {'STRAIGHT': file, 'LEFT': file, 'RIGHT': file}.
    Each photo must show exactly one clear face, the three must be the same
    person, and that person must not already be registered as someone else.
    """
    engine = get_engine()
    chosen = []
    for pose, label in POSE_LABELS.items():
        image = read_image(uploads[pose], f'{label} photo')
        faces = engine.analyze(image, max_side=SELFIE_MAX_SIDE)
        if not faces:
            raise FaceError(
                f'No face found in the {label} photo. Make sure your face is well lit and inside the oval.'
            )
        faces.sort(key=_area, reverse=True)
        if len(faces) > 1 and _area(faces[1]) >= 0.5 * _area(faces[0]):
            raise FaceError(f'More than one face in the {label} photo. Only you should be in the picture.')
        main = faces[0]
        if main.box[2] - main.box[0] < MIN_FACE_PIXELS:
            raise FaceError(f'Your face is too small in the {label} photo. Move closer to the camera.')
        chosen.append((pose, main))

    vectors = np.stack([face.embedding for _, face in chosen])
    agreement = vectors @ vectors.T
    if agreement[np.triu_indices(len(vectors), 1)].min() < settings.FACE_SAME_PERSON_THRESHOLD:
        raise FaceError("The 3 photos don't look like the same person. Please try again.")

    # Is this face already registered to another student?
    others = StudentFace.objects.exclude(student=student).filter(
        student__user__deleted=False,
    ).values_list('embedding', flat=True)
    if others:
        gallery = np.stack([embedding_from_bytes(e) for e in others])
        if float((gallery @ vectors.T).max()) >= settings.FACE_DUPLICATE_THRESHOLD:
            raise FaceError(
                'This face is already registered to another student. Please contact an admin.', status=409
            )

    now = timezone.now()
    with transaction.atomic():
        StudentFace.objects.filter(student=student).delete()
        StudentFace.objects.bulk_create([
            StudentFace(
                student=student, pose=pose, crop_jpeg=crop_to_jpeg(face.crop),
                embedding=embedding_to_bytes(face.embedding), engine=engine.name, consented_at=now,
            )
            for pose, face in chosen
        ])
    return face_status(student)


# ── Class photos ──────────────────────────────────────────────────────────────

def recognize_class(course_info, uploads: list) -> dict:
    """
    Finds the course's enrolled students in 1-3 class photos. Returns a
    suggestion per student (present / unsure / absent) plus faces that matched
    nobody. Nothing is saved: the teacher confirms with save_face_attendance().
    """
    engine = get_engine()
    students = [
        m.student for m in StudentClassroom.objects.filter(
            classroom=course_info.classroom, student__user__deleted=False,
        ).current().select_related('student__user').order_by('student__student_id')
    ]
    index_of = {s.id: i for i, s in enumerate(students)}

    # Gallery: every registered face of these students
    gallery, owner = [], []
    for row in StudentFace.objects.filter(student__in=students).only('student_id', 'embedding'):
        gallery.append(embedding_from_bytes(row.embedding))
        owner.append(index_of[row.student_id])
    registered = set(owner)

    photos, detections = [], []
    for photo_index, upload in enumerate(uploads):
        image = read_image(upload, f'photo {photo_index + 1}')
        photos.append({'index': photo_index, 'width': image.shape[1], 'height': image.shape[0]})
        for face in engine.analyze(image, max_side=settings.FACE_CLASS_PHOTO_MAX_SIDE):
            detections.append((photo_index, face))

    # scores[f, s] = best similarity of face f to any registered photo of student s
    scores = np.full((len(detections), len(students)), -1.0, dtype=np.float32)
    if detections and gallery:
        sims = np.stack([f.embedding for _, f in detections]) @ np.stack(gallery).T
        for col, student_index in enumerate(owner):
            scores[:, student_index] = np.maximum(scores[:, student_index], sims[:, col])

    # Give each face at most one student, most confident faces first.
    low, high = settings.FACE_UNSURE_THRESHOLD, settings.FACE_MATCH_THRESHOLD
    match = {}         # student index -> (score, detection index)
    unknown = []
    order = sorted(range(len(detections)), key=lambda f: -scores[f].max() if students else 0)
    for f in order:
        photo = detections[f][0]
        candidates = [int(s) for s in np.argsort(-scores[f]) if scores[f, s] >= low] if students else []
        if not candidates:
            unknown.append(f)
            continue
        best = candidates[0]
        if best not in match:
            match[best] = (float(scores[f, best]), f)
        elif detections[match[best][1]][0] != photo:
            # The best student was already found in another photo: almost always the
            # same person photographed twice, so this face adds nothing.
            continue
        else:
            # Two faces in one photo are two people: try this face's next candidates.
            free = [s for s in candidates[1:] if s not in match]
            if free:
                match[free[0]] = (float(scores[f, free[0]]), f)
            else:
                unknown.append(f)

    def face_info(f):
        photo_index, face = detections[f]
        return {'photo': photo_index, 'box': [round(v) for v in face.box]}

    rows = []
    for i, student in enumerate(students):
        row = {
            'id': str(student.id),
            'student_id': student.student_id,
            'name': student.user.get_full_name() or student.user.username,
            'score': None, 'face': None, 'crop': None,
        }
        if i in match:
            score, f = match[i]
            row.update(
                score=round(score, 3), face=face_info(f), crop=jpeg_data_url(crop_to_jpeg(detections[f][1].crop)),
                status='present' if score >= high else 'unsure',
                reason='match' if score >= high else 'low_match',
            )
        elif i not in registered:
            row.update(status='unsure', reason='no_face')
        else:
            row.update(status='absent', reason='not_found')
        rows.append(row)

    unknown_faces = [
        {**face_info(f), 'crop': jpeg_data_url(crop_to_jpeg(detections[f][1].crop))} for f in unknown
    ]
    counts = {s: sum(r['status'] == s for r in rows) for s in ('present', 'unsure', 'absent')}
    return {
        'photos': photos,
        'students': rows,
        'unknown_faces': unknown_faces,
        'summary': {**counts, 'unknown': len(unknown_faces), 'faces_found': len(detections)},
    }


def save_face_attendance(course_info, present_ids, day=None) -> AttendanceSession:
    """Saves the teacher-confirmed result as a finished FACE session."""
    now = timezone.now()
    day = day or timezone.localdate()
    enrolled = [
        m.student for m in StudentClassroom.objects.filter(
            classroom=course_info.classroom
        ).enrolled_on(day).active_accounts().select_related('student')
    ]
    enrolled_ids = {str(s.id) for s in enrolled}
    present_ids = {str(i) for i in present_ids}
    if not present_ids <= enrolled_ids:
        raise FaceError('Some selected students are not in this course.')

    with transaction.atomic():
        # Taking face attendance again the same day replaces the earlier face result.
        AttendanceSession.objects.filter(
            course_info=course_info, date=day, mode=AttendanceSession.Mode.FACE,
        ).delete()
        session = AttendanceSession.objects.create(
            course_info=course_info,
            date=day,
            mode=AttendanceSession.Mode.FACE,
            is_active=False,
            ended_at=now,
            duration_seconds=0,
        )
        time_now = timezone.localtime(now).time()
        AttendanceLog.objects.bulk_create([
            AttendanceLog(
                session=session, course_info=course_info, student=student, date=session.date,
                time=time_now, source=AttendanceLog.Source.FACE,
                status=AttendanceLog.Status.PRESENT if str(student.id) in present_ids
                else AttendanceLog.Status.ABSENT,
                method=AttendanceLog.Method.FACE if str(student.id) in present_ids else '',
            )
            for student in enrolled
        ])
    return session
