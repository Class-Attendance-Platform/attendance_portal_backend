from django.core.cache import cache
from django.test import TestCase

from apps.attendance import redis_service
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.tests.helpers import (
    client_for, expire_session, make_course_info, make_student, make_teacher,
)


class QRSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_teacher('teacher@example.com')
        self.student_a = make_student('a@example.com', 2302001)
        self.student_b = make_student('b@example.com', 2302002)
        self.ci = make_course_info(self.teacher, students=[self.student_a, self.student_b])
        self.teacher_client = client_for(self.teacher.user)

        res = self.teacher_client.post('/api/sessions/start/', {
            'course_info_id': str(self.ci.id), 'mode': 'QR_ONLINE', 'duration_seconds': 60,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.session_id = res.data['session']['id']
        self.qr_token = res.data['session']['qr_token']

    def checkin(self, student, student_id=None):
        return client_for(student.user).post(f'/api/sessions/{self.session_id}/checkin/', {
            'student_id': student_id or student.student_id,
            'mac_address': f'web{student.student_id:014d}',
            'qr_token': self.qr_token,
        }, format='json')

    def status_of(self, student):
        log = AttendanceLog.objects.get(session_id=self.session_id, student=student)
        return log.status

    def test_stop_after_timer_ends_keeps_checkins(self):
        self.assertEqual(self.checkin(self.student_a).status_code, 200)
        expire_session(self.session_id)

        res = self.teacher_client.post(f'/api/sessions/{self.session_id}/stop/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.status_of(self.student_a), 'PRESENT')
        self.assertEqual(self.status_of(self.student_b), 'ABSENT')

    def test_status_poll_after_timer_ends_saves_checkins(self):
        self.assertEqual(self.checkin(self.student_a).status_code, 200)
        expire_session(self.session_id)

        res = self.teacher_client.get(f'/api/sessions/{self.session_id}/status/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['active'])
        self.assertEqual(self.status_of(self.student_a), 'PRESENT')
        self.assertEqual(self.status_of(self.student_b), 'ABSENT')

        # The app still calls stop when its own timer hits zero: must not fail.
        res = self.teacher_client.post(f'/api/sessions/{self.session_id}/stop/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.status_of(self.student_a), 'PRESENT')

    def test_manual_stop_before_timer_ends(self):
        self.assertEqual(self.checkin(self.student_a).status_code, 200)
        res = self.teacher_client.post(f'/api/sessions/{self.session_id}/stop/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['total_present'], 1)
        self.assertEqual(self.status_of(self.student_a), 'PRESENT')
        self.assertEqual(self.status_of(self.student_b), 'ABSENT')

    def test_checkin_after_timer_ends_is_rejected(self):
        expire_session(self.session_id)
        self.assertEqual(self.checkin(self.student_a).status_code, 410)

    def test_student_cannot_check_in_for_someone_else(self):
        res = self.checkin(self.student_b, student_id=self.student_a.student_id)
        self.assertEqual(res.status_code, 403)
        self.assertNotIn(str(self.student_a.student_id), redis_service.get_submissions(self.session_id))

    def test_student_not_in_class_cannot_check_in(self):
        outsider = make_student('c@example.com', 2302003)
        self.assertEqual(self.checkin(outsider).status_code, 403)

    def test_manual_mark_survives_stop(self):
        res = self.teacher_client.post(f'/api/sessions/{self.session_id}/mark/', {
            'student_id': str(self.student_b.id), 'status': 'LATE',
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.teacher_client.post(f'/api/sessions/{self.session_id}/stop/')
        self.assertEqual(self.status_of(self.student_b), 'LATE')


class FingerprintSessionTests(TestCase):
    def test_fingerprint_logs_use_hardware_source(self):
        cache.clear()
        teacher = make_teacher('teacher@example.com')
        student = make_student('a@example.com', 2302001)
        ci = make_course_info(teacher, students=[student])
        client = client_for(teacher.user)

        res = client.post('/api/sessions/start/', {
            'course_info_id': str(ci.id), 'mode': 'FINGERPRINT', 'duration_seconds': 60,
        }, format='json')
        session_id = res.data['session']['id']
        redis_service.add_submission(session_id, student.student_id, 'Student', 'hardware')
        client.post(f'/api/sessions/{session_id}/stop/')

        log = AttendanceLog.objects.get(session_id=session_id, student=student)
        self.assertEqual(log.status, 'PRESENT')
        self.assertEqual(log.source, AttendanceLog.Source.HARDWARE)


class TeacherOwnershipTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_teacher('owner@example.com')
        self.other = make_teacher('other@example.com')
        self.student = make_student('a@example.com', 2302001)
        self.ci = make_course_info(self.owner, students=[self.student])
        self.owner_client = client_for(self.owner.user)
        self.other_client = client_for(self.other.user)

    def test_other_teacher_cannot_start_session(self):
        res = self.other_client.post('/api/sessions/start/', {
            'course_info_id': str(self.ci.id), 'mode': 'QR_ONLINE',
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(AttendanceSession.objects.exists())

    def test_other_teacher_cannot_control_owners_session(self):
        res = self.owner_client.post('/api/sessions/start/', {
            'course_info_id': str(self.ci.id), 'mode': 'QR_ONLINE',
        }, format='json')
        session_id = res.data['session']['id']

        self.assertEqual(self.other_client.get(f'/api/sessions/{session_id}/status/').status_code, 403)
        self.assertEqual(self.other_client.post(f'/api/sessions/{session_id}/mark/', {
            'student_id': str(self.student.id), 'status': 'PRESENT',
        }, format='json').status_code, 403)
        self.assertEqual(self.other_client.post(f'/api/sessions/{session_id}/stop/').status_code, 403)
        self.assertTrue(AttendanceSession.objects.get(id=session_id).is_active)

    def test_other_teacher_cannot_read_or_edit_course(self):
        ci = self.ci.id
        self.assertEqual(self.other_client.get(f'/api/teacher/course-info/{ci}/').status_code, 403)
        self.assertEqual(self.other_client.get(f'/api/sessions/course-info/{ci}/history/').status_code, 403)
        self.assertEqual(self.other_client.post(f'/api/teacher/course-info/{ci}/history-session/', {
            'date': '2026-10-01', 'presentStudentIds': [],
        }, format='json').status_code, 403)
        self.assertEqual(
            self.other_client.delete(f'/api/teacher/course-info/{ci}/history-session/2026-10-01/').status_code, 403
        )
        self.assertEqual(
            self.other_client.get(f'/api/reports/course-info/{ci}/export/?export_format=csv').status_code, 403
        )
        self.assertFalse(AttendanceLog.objects.exists())

    def test_teacher_cannot_list_another_teachers_courses(self):
        res = self.other_client.get(f'/api/teacher/{self.owner.user.id}/courses/')
        self.assertEqual(res.status_code, 403)

    def test_owner_has_access(self):
        ci = self.ci.id
        self.assertEqual(self.owner_client.get(f'/api/teacher/{self.owner.user.id}/courses/').status_code, 200)
        self.assertEqual(self.owner_client.get(f'/api/teacher/course-info/{ci}/').status_code, 200)
        self.assertEqual(self.owner_client.get(f'/api/sessions/course-info/{ci}/history/').status_code, 200)
        res = self.owner_client.get(f'/api/reports/course-info/{ci}/export/?export_format=csv')
        self.assertEqual(res.status_code, 200)
        self.assertIn('2302001', res.content.decode())


class SimultaneousCheckinTests(TestCase):
    def test_no_checkin_is_lost_when_many_arrive_at_once(self):
        import threading

        cache.clear()
        session_id = 'concurrency-test'
        redis_service.create_session_cache(session_id, 'ci', 'QR_ONLINE', 60)

        threads = [
            threading.Thread(target=redis_service.add_submission, args=(session_id, 2302000 + i, 'S', 'mac'))
            for i in range(40)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(redis_service.get_submissions(session_id)), 40)


class SessionEdgeCaseTests(TestCase):
    """Cases found in review: start races, busy locks, Redis trouble, enrolment checks."""

    def setUp(self):
        cache.clear()
        self.teacher = make_teacher('teacher@example.com')
        self.student = make_student('a@example.com', 2302001)
        self.ci = make_course_info(self.teacher, students=[self.student])
        self.teacher_client = client_for(self.teacher.user)
        self.student_client = client_for(self.student.user)

    def start(self):
        res = self.teacher_client.post('/api/sessions/start/', {
            'course_info_id': str(self.ci.id), 'mode': 'QR_ONLINE', 'duration_seconds': 60,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        return res.data['session']

    def test_lookup_while_session_has_no_live_data_yet_keeps_it_running(self):
        session = AttendanceSession.objects.create(
            course_info=self.ci, date='2026-10-05', mode='QR_ONLINE', duration_seconds=60, qr_token='t',
        )
        res = self.student_client.get(f'/api/sessions/course-info/{self.ci.id}/active/')
        self.assertTrue(res.data['success'])
        session.refresh_from_db()
        self.assertTrue(session.is_active)
        self.assertFalse(AttendanceLog.objects.exists())

    def test_status_without_live_data_but_timer_running_stays_active(self):
        session = self.start()
        redis_service.delete_session_cache(session['id'])
        res = self.teacher_client.get(f'/api/sessions/{session["id"]}/status/')
        self.assertTrue(res.data['active'])
        self.assertGreater(res.data['session']['time_left'], 0)

    def test_active_session_hidden_from_students_not_in_course(self):
        self.start()
        outsider = make_student('c@example.com', 2302003)
        res = client_for(outsider.user).get(f'/api/sessions/course-info/{self.ci.id}/active/')
        self.assertFalse(res.data['success'])
        self.assertNotIn('qr_token', res.data)

    def test_manual_mark_rejects_student_from_another_class(self):
        session = self.start()
        outsider = make_student('c@example.com', 2302003)
        res = self.teacher_client.post(f'/api/sessions/{session["id"]}/mark/', {
            'student_id': str(outsider.id), 'status': 'PRESENT',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(AttendanceLog.objects.filter(student=outsider).exists())

    def test_checkin_returns_503_when_too_busy(self):
        from unittest import mock
        session = self.start()
        with mock.patch.object(redis_service, 'add_submission', side_effect=redis_service.SessionBusy):
            res = self.student_client.post(f'/api/sessions/{session["id"]}/checkin/', {
                'mac_address': 'web00000000000001', 'qr_token': session['qr_token'],
            }, format='json')
        self.assertEqual(res.status_code, 503)

    def test_course_pages_still_load_when_redis_fails(self):
        from unittest import mock
        session = self.start()
        with mock.patch.object(redis_service, 'get_session_cache', side_effect=ConnectionError('redis down')):
            res = self.teacher_client.get(f'/api/teacher/course-info/{self.ci.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AttendanceSession.objects.get(id=session['id']).is_active)

    def test_opening_the_teacher_dashboard_saves_ended_sessions(self):
        session = self.start()
        client_for(self.student.user).post(f'/api/sessions/{session["id"]}/checkin/', {
            'mac_address': 'web00000000000001', 'qr_token': session['qr_token'],
        }, format='json')
        expire_session(session['id'])

        res = self.teacher_client.get(f'/api/teacher/{self.teacher.user.id}/courses/')

        self.assertEqual(res.status_code, 200)
        self.assertFalse(AttendanceSession.objects.get(id=session['id']).is_active)
        self.assertEqual(AttendanceLog.objects.get(student=self.student).status, 'PRESENT')
