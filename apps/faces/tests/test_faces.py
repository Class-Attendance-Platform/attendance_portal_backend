import io
import unittest
from unittest import mock

import numpy as np
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.tests.helpers import (
    client_for, make_admin, make_course_info, make_student, make_teacher,
)
from apps.faces.engines import DetectedFace, FaceEngineUnavailable
from apps.faces.models import StudentFace
from apps.faces.services import embedding_to_bytes


def person(n, noise=0.0, seed=0):
    """A unit-length fingerprint for person n; noise makes a slightly different photo of them."""
    vector = np.zeros(512, dtype=np.float32)
    vector[n] = 1.0
    if noise:
        jitter = np.random.default_rng(seed).normal(0, 1, 512).astype(np.float32)
        vector += noise * jitter / np.linalg.norm(jitter)
    return vector / np.linalg.norm(vector)


def blend(a, b, weight_b):
    """A fingerprint whose similarity to `a` is lowered by mixing in `b`."""
    vector = (1 - weight_b) * person(a) + weight_b * person(b)
    return vector / np.linalg.norm(vector)


def face(embedding, size=120, x=10):
    return DetectedFace(
        box=(x, 10, x + size, 10 + size), score=0.9, embedding=embedding,
        crop=np.zeros((112, 112, 3), dtype=np.uint8),
    )


class FakeEngine:
    """Returns preset faces for each photo, keyed by the photo's solid colour."""
    name = 'fake'

    def __init__(self):
        self.faces_by_color = {}

    def photo(self, color, faces):
        self.faces_by_color[color] = faces
        image = Image.new('RGB', (200, 200), color)
        data = io.BytesIO()
        image.save(data, format='PNG')
        return SimpleUploadedFile(f'{color}.png', data.getvalue(), content_type='image/png')

    def analyze(self, image, max_side=640):
        b, g, r = (int(v) for v in image[0, 0])
        return self.faces_by_color.get((r, g, b), [])


class FaceTestCase(TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        patcher = mock.patch('apps.faces.services.get_engine', return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.colors = iter((i, 0, 0) for i in range(1, 250))

    def photo(self, *faces):
        return self.engine.photo(next(self.colors), list(faces))

    def register(self, student, straight, left, right, consent='true'):
        return client_for(student.user).post('/api/faces/me/', {
            'straight': self.photo(*straight), 'left': self.photo(*left), 'right': self.photo(*right),
            'consent': consent,
        }, format='multipart')

    def register_ok(self, student, n):
        res = self.register(student, [face(person(n, 0.1, 1))], [face(person(n, 0.1, 2))], [face(person(n, 0.1, 3))])
        self.assertEqual(res.status_code, 201, res.data)


class RegistrationTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student('a@example.com', 2302001)

    def test_register_three_photos(self):
        self.register_ok(self.student, 1)
        self.assertEqual(StudentFace.objects.filter(student=self.student).count(), 3)
        res = client_for(self.student.user).get('/api/faces/me/')
        self.assertTrue(res.data['registered'])
        self.assertCountEqual(res.data['poses'], ['STRAIGHT', 'LEFT', 'RIGHT'])

    def test_consent_is_required(self):
        res = self.register(self.student, [face(person(1))], [face(person(1))], [face(person(1))], consent='')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(StudentFace.objects.exists())

    def test_all_three_photos_are_required(self):
        res = client_for(self.student.user).post('/api/faces/me/', {
            'straight': self.photo(face(person(1))), 'consent': 'true',
        }, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertIn('left', res.data['message'])

    def test_photo_without_face_is_rejected(self):
        res = self.register(self.student, [face(person(1))], [], [face(person(1))])
        self.assertEqual(res.status_code, 400)
        self.assertIn('No face found in the left photo', res.data['message'])

    def test_photo_with_two_people_is_rejected(self):
        res = self.register(self.student, [face(person(1)), face(person(2), x=150)], [face(person(1))], [face(person(1))])
        self.assertEqual(res.status_code, 400)
        self.assertIn('More than one face', res.data['message'])

    def test_small_background_face_is_ignored(self):
        res = self.register(
            self.student, [face(person(1)), face(person(2), size=40, x=150)], [face(person(1))], [face(person(1))],
        )
        self.assertEqual(res.status_code, 201)

    def test_face_too_small_is_rejected(self):
        res = self.register(self.student, [face(person(1), size=40)], [face(person(1))], [face(person(1))])
        self.assertEqual(res.status_code, 400)
        self.assertIn('too small', res.data['message'])

    def test_photos_of_different_people_are_rejected(self):
        res = self.register(self.student, [face(person(1))], [face(person(2))], [face(person(1))])
        self.assertEqual(res.status_code, 400)
        self.assertIn('same person', res.data['message'])

    def test_face_registered_to_another_student_is_blocked(self):
        other = make_student('b@example.com', 2302002)
        self.register_ok(other, 1)
        res = self.register(self.student, [face(person(1, 0.1, 7))], [face(person(1))], [face(person(1))])
        self.assertEqual(res.status_code, 409)
        self.assertIn('contact an admin', res.data['message'])
        self.assertFalse(StudentFace.objects.filter(student=self.student).exists())

    def test_registering_again_replaces_old_photos(self):
        self.register_ok(self.student, 1)
        self.register_ok(self.student, 1)
        self.assertEqual(StudentFace.objects.filter(student=self.student).count(), 3)

    def test_student_can_delete_face_data(self):
        self.register_ok(self.student, 1)
        res = client_for(self.student.user).delete('/api/faces/me/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(StudentFace.objects.exists())

    def test_only_students_can_register(self):
        teacher = make_teacher('t@example.com')
        self.assertEqual(client_for(teacher.user).get('/api/faces/me/').status_code, 403)

    def test_engine_not_set_up_gives_clear_error(self):
        with mock.patch('apps.faces.services.get_engine', side_effect=FaceEngineUnavailable('no models')):
            res = self.register(self.student, [face(person(1))], [face(person(1))], [face(person(1))])
        self.assertEqual(res.status_code, 503)
        self.assertIn('not set up', res.data['message'])


class ClassPhotoTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = make_teacher('teacher@example.com')
        self.students = [make_student(f's{i}@example.com', 2302000 + i) for i in range(1, 6)]
        self.ci = make_course_info(self.teacher, students=self.students)
        for i, student in enumerate(self.students[:4], start=1):
            self.register_ok(student, i)  # student 5 never registered a face
        self.client = client_for(self.teacher.user)

    def recognize(self, *photos, client=None):
        return (client or self.client).post('/api/faces/recognize/', {
            'course_info_id': str(self.ci.id), 'photos': list(photos),
        }, format='multipart')

    def by_number(self, res):
        return {row['student_id']: row for row in res.data['students']}

    def test_suggests_present_unsure_absent_and_unknown(self):
        photo = self.photo(
            face(person(1, 0.1, 9)),            # student 1: clear match
            face(blend(2, 100, 0.68), x=150),   # student 2: weak match (~0.38)
            face(person(200), x=300),           # a stranger
        )                                       # student 3 and 4 not in the photo
        res = self.recognize(photo)

        self.assertEqual(res.status_code, 200, res.data)
        rows = self.by_number(res)
        self.assertEqual((rows[2302001]['status'], rows[2302001]['reason']), ('present', 'match'))
        self.assertEqual((rows[2302002]['status'], rows[2302002]['reason']), ('unsure', 'low_match'))
        self.assertEqual((rows[2302003]['status'], rows[2302003]['reason']), ('absent', 'not_found'))
        self.assertEqual((rows[2302005]['status'], rows[2302005]['reason']), ('unsure', 'no_face'))
        self.assertTrue(rows[2302001]['crop'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(rows[2302001]['face'], {'photo': 0, 'box': [10, 10, 130, 130]})
        self.assertEqual(len(res.data['unknown_faces']), 1)
        self.assertEqual(res.data['summary'], {'present': 1, 'unsure': 2, 'absent': 2, 'unknown': 1, 'faces_found': 3})
        self.assertFalse(AttendanceLog.objects.exists())  # nothing saved until confirmed

    def test_student_in_two_photos_is_counted_once(self):
        front = self.photo(face(person(1, 0.1, 11)))
        back = self.photo(face(person(1, 0.1, 12)), face(person(3, 0.1, 13), x=150))
        res = self.recognize(front, back)
        rows = self.by_number(res)
        self.assertEqual(rows[2302001]['status'], 'present')
        self.assertEqual(rows[2302003]['status'], 'present')
        self.assertEqual(res.data['unknown_faces'], [])

    def test_photo_count_and_access_checks(self):
        self.assertEqual(self.recognize().status_code, 400)
        self.assertEqual(self.recognize(*[self.photo() for _ in range(4)]).status_code, 400)
        other = client_for(make_teacher('other@example.com').user)
        self.assertEqual(self.recognize(self.photo(), client=other).status_code, 403)
        res = self.client.post('/api/faces/recognize/', {'course_info_id': 'not-a-uuid'}, format='multipart')
        self.assertEqual(res.status_code, 404)

    def test_unreadable_photo_is_rejected(self):
        bad = SimpleUploadedFile('x.jpg', b'not an image', content_type='image/jpeg')
        res = self.recognize(bad)
        self.assertEqual(res.status_code, 400)
        self.assertIn('Could not read', res.data['message'])

    def confirm(self, present, client=None, **extra):
        return (client or self.client).post('/api/faces/confirm/', {
            'course_info_id': str(self.ci.id), 'present_student_ids': present, **extra,
        }, format='json')

    def test_confirm_saves_face_attendance(self):
        res = self.confirm([str(self.students[0].id), str(self.students[4].id)], date='2026-10-05')

        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['total_present'], 2)
        session = AttendanceSession.objects.get(id=res.data['session_id'])
        self.assertEqual((session.mode, session.is_active, str(session.date)), ('FACE', False, '2026-10-05'))
        statuses = dict(AttendanceLog.objects.filter(session=session).values_list('student__student_id', 'status'))
        self.assertEqual(statuses, {2302001: 'PRESENT', 2302002: 'ABSENT', 2302003: 'ABSENT', 2302004: 'ABSENT', 2302005: 'PRESENT'})
        self.assertEqual(set(AttendanceLog.objects.values_list('source', flat=True)), {'FACE'})

    def test_confirm_rejects_students_from_other_classes(self):
        outsider = make_student('x@example.com', 2399999)
        res = self.confirm([str(outsider.id)])
        self.assertEqual(res.status_code, 400)
        self.assertFalse(AttendanceSession.objects.exists())

    def test_other_teacher_cannot_confirm(self):
        other = client_for(make_teacher('other@example.com').user)
        self.assertEqual(self.confirm([], client=other).status_code, 403)

    def test_live_sessions_cannot_use_face_mode(self):
        res = self.client.post('/api/sessions/start/', {
            'course_info_id': str(self.ci.id), 'mode': 'FACE',
        }, format='json')
        self.assertEqual(res.status_code, 400)


class AdminFaceTests(FaceTestCase):
    def test_admin_sees_views_and_resets_faces(self):
        admin = client_for(make_admin('admin@example.com'))
        student = make_student('a@example.com', 2302001)
        self.register_ok(student, 1)

        res = admin.get('/api/faces/admin/students/')
        self.assertIn(str(student.id), res.data['registered'])

        res = admin.get(f'/api/faces/admin/students/{student.id}/')
        self.assertEqual(len(res.data['crops']), 3)

        res = admin.delete(f'/api/faces/admin/students/{student.id}/')
        self.assertEqual(res.data['deleted'], 3)
        self.assertFalse(StudentFace.objects.exists())

    def test_teachers_cannot_use_admin_face_endpoints(self):
        teacher = client_for(make_teacher('t@example.com').user)
        self.assertEqual(teacher.get('/api/faces/admin/students/').status_code, 403)


@unittest.skipUnless(
    (settings.FACE_MODEL_DIR / 'det_10g.onnx').is_file(), 'face models not downloaded'
)
class RealEngineSmokeTests(TestCase):
    def test_engine_loads_and_handles_a_photo_without_faces(self):
        from apps.faces.engines.insightface_onnx import InsightFaceOnnxEngine
        engine = InsightFaceOnnxEngine(settings.FACE_MODEL_DIR)
        self.assertEqual(engine.analyze(np.full((480, 640, 3), 128, dtype=np.uint8)), [])
        self.assertEqual(len(embedding_to_bytes(person(1))), 2048)
