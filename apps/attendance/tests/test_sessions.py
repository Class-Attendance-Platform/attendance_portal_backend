"""Live sessions with the rotating 6-digit code (API v2 section 5)."""
import time
import uuid
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.academic.models import Semester, StudentClassroom
from apps.attendance import codes, redis_service
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.services import _lock_course, finalize_session
from apps.attendance.tests.helpers import (
    check_in, client_for, current_code, expire_session, make_admin, make_course_info, make_student,
    make_teacher, start_session,
)
from apps.users.models import DeviceBinding


class LiveTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_teacher('teacher@example.com')
        self.student_a = make_student('a@example.com', 2302001, first_name='Ana', last_name='Rahman')
        self.student_b = make_student('b@example.com', 2302002)
        self.ci = make_course_info(self.teacher, students=[self.student_a, self.student_b])
        self.teacher_client = client_for(self.teacher.user)

    def start(self, **body):
        res = start_session(self.teacher_client, self.ci, **body)
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['session']['id']

    def log_of(self, session_id, student):
        return AttendanceLog.objects.get(session_id=session_id, student=student)


class CodeTests(TestCase):
    def test_six_digits_from_the_secret_and_the_window(self):
        code = codes.code_for('secret', 12345)
        self.assertRegex(code, r'^\d{6}$')
        self.assertEqual(code, codes.code_for('secret', 12345))
        self.assertNotEqual(code, codes.code_for('other secret', 12345))

    def test_current_and_previous_window_are_accepted(self):
        now = 1_800_000_015.0
        window = codes.window(now)
        self.assertTrue(codes.code_matches('s', codes.code_for('s', window), now))
        self.assertTrue(codes.code_matches('s', codes.code_for('s', window - 1), now))
        self.assertFalse(codes.code_matches('s', codes.code_for('s', window - 2), now))
        self.assertFalse(codes.code_matches('s', codes.code_for('s', window + 1), now))
        self.assertFalse(codes.code_matches('', '123456', now))

    def test_non_ascii_text_never_matches_or_raises(self):
        self.assertFalse(codes.code_matches('s', '১২৩৪৫৬'))
        self.assertFalse(codes.code_matches('s', 'é23456'))

    def test_expires_in_counts_down_to_the_next_window(self):
        self.assertEqual(codes.current_code('s', now=1_800_000_010.0)[1], 20)  # 1_800_000_000 is a window start
        self.assertEqual(codes.current_code('s', now=1_800_000_029.5)[1], 1)

    @override_settings(WEB_URL='https://portal.example')
    def test_check_in_url(self):
        self.assertEqual(codes.check_in_url('abc', '012345'), 'https://portal.example/check-in?s=abc&c=012345')


BENGALI_DIGITS = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')


class StartSessionTests(LiveTestCase):
    def test_not_for_a_course_of_a_deleted_semester(self):
        Semester.objects.filter(id=self.ci.semester_id).update(deleted=True)
        self.assertEqual(start_session(self.teacher_client, self.ci).status_code, 404)
        self.assertFalse(AttendanceSession.objects.exists())

    def test_start_returns_the_session(self):
        res = start_session(self.teacher_client, self.ci, delivery='ONLINE', duration_minutes=10)
        self.assertEqual(res.status_code, 201)
        session = res.data['session']
        self.assertEqual(
            set(session), {'id', 'course_info_id', 'delivery', 'date', 'started_at', 'ends_at', 'time_left',
                           'code_period'},
        )
        self.assertEqual((session['course_info_id'], session['delivery'], session['code_period']),
                         (str(self.ci.id), 'ONLINE', 30))
        self.assertTrue(595 <= session['time_left'] <= 600)
        self.assertNotIn('qr_token', session)
        row = AttendanceSession.objects.get(id=session['id'])
        self.assertEqual((row.delivery, row.duration_seconds, row.mode), ('ONLINE', 600, 'QR_ONLINE'))
        self.assertTrue(row.qr_token)

    def test_delivery_defaults_to_in_class(self):
        session_id = self.start()
        self.assertEqual(AttendanceSession.objects.get(id=session_id).delivery, 'IN_CLASS')

    def test_only_the_offered_lengths(self):
        for minutes in (2, 5, 10, 15):
            cache.clear()
            AttendanceSession.objects.all().delete()
            self.assertEqual(start_session(self.teacher_client, self.ci, duration_minutes=minutes).status_code, 201)
        res = start_session(self.teacher_client, self.ci, duration_minutes=7)
        self.assertEqual((res.status_code, res.data['message']), (400, 'Choose 2, 5, 10 or 15 minutes.'))
        res = start_session(self.teacher_client, self.ci, delivery='SOMEWHERE')
        self.assertEqual(res.status_code, 400)

    def test_a_live_session_blocks_a_second_one(self):
        session_id = self.start()
        res = start_session(self.teacher_client, self.ci)
        self.assertEqual((res.status_code, res.data['code'], res.data['session_id']),
                         (409, 'session_running', session_id))
        self.assertEqual(AttendanceSession.objects.count(), 1)

    def test_an_ended_session_is_saved_before_the_next_starts(self):
        first = self.start()
        check_in(self.student_a, first)
        expire_session(first)
        second = self.start()
        self.assertNotEqual(first, second)
        self.assertFalse(AttendanceSession.objects.get(id=first).is_active)
        self.assertEqual(self.log_of(first, self.student_a).status, 'PRESENT')

    def test_admin_may_start(self):
        res = start_session(client_for(make_admin('admin@example.com')), self.ci)
        self.assertEqual(res.status_code, 201)

    def test_fingerprint_mode_still_works_for_the_hidden_devices(self):
        session_id = self.start(mode='FINGERPRINT')
        self.assertIsNone(AttendanceSession.objects.get(id=session_id).qr_token)
        res = self.teacher_client.get(f'/api/sessions/{session_id}/code/')
        self.assertEqual((res.status_code, res.data['code']), (400, 'no_code'))


class SessionCodeTests(LiveTestCase):
    @override_settings(WEB_URL='https://portal.example')
    def test_code_and_link(self):
        session_id = self.start()
        res = self.teacher_client.get(f'/api/sessions/{session_id}/code/')
        self.assertEqual(res.status_code, 200)
        code = current_code(session_id)
        self.assertEqual(res.data['code'], code)
        self.assertEqual(res.data['check_in_url'], f'https://portal.example/check-in?s={session_id}&c={code}')
        self.assertEqual(res.data['period'], 30)
        self.assertTrue(1 <= res.data['expires_in'] <= 30)

    def test_students_and_other_teachers_cannot_read_the_code(self):
        session_id = self.start()
        self.assertEqual(client_for(self.student_a.user).get(f'/api/sessions/{session_id}/code/').status_code, 403)
        other = client_for(make_teacher('other@example.com').user)
        self.assertEqual(other.get(f'/api/sessions/{session_id}/code/').status_code, 403)

    def test_ended_session_has_no_code_and_is_saved(self):
        session_id = self.start()
        expire_session(session_id)
        res = self.teacher_client.get(f'/api/sessions/{session_id}/code/')
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))
        self.assertFalse(AttendanceSession.objects.get(id=session_id).is_active)


class CheckInTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.session_id = self.start()

    def test_qr_check_in(self):
        res = check_in(self.student_a, self.session_id)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['message'], 'Checked in to CSE301.')
        self.assertEqual(res.data['course'], {'code': 'CSE301', 'title': 'Course CSE301'})
        self.assertIn('+06:00', res.data['time'])
        submission = redis_service.get_submissions(self.session_id)['2302001']
        self.assertEqual((submission['method'], submission['device_id']), ('QR', 'device-2302001'))
        self.assertFalse(DeviceBinding.objects.exists())  # no permanent binding

    def test_typed_code_finds_the_students_live_session(self):
        res = check_in(self.student_a, code=current_code(self.session_id))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(redis_service.get_submissions(self.session_id)['2302001']['method'], 'CODE')

    def test_previous_code_still_works_older_ones_do_not(self):
        token = AttendanceSession.objects.get(id=self.session_id).qr_token
        now = time.time()
        with mock.patch('time.time', return_value=now):  # no window change during the test
            previous = codes.code_for(token, codes.window() - 1)
            older = codes.code_for(token, codes.window() - 2)
            if older in (previous, codes.code_for(token, codes.window())):  # pragma: no cover - 1 in 500k
                self.skipTest('codes collided')
            res = check_in(self.student_a, self.session_id, code=older)
            self.assertEqual((res.status_code, res.data['code']), (400, 'code_invalid'))
            self.assertEqual(check_in(self.student_a, self.session_id, code=previous).status_code, 200)

    def test_wrong_code(self):
        wrong = '000000' if current_code(self.session_id) != '000000' else '111111'
        for res in (check_in(self.student_a, self.session_id, code=wrong),
                    check_in(self.student_a, code=wrong),
                    check_in(self.student_a, self.session_id, code='12ab')):
            self.assertEqual((res.status_code, res.data['code']), (400, 'code_invalid'))
            self.assertEqual(res.data['message'], 'This code is wrong or has expired. Check the newest code.')
        self.assertEqual(redis_service.get_submissions(self.session_id), {})

    def test_code_with_spaces_is_accepted(self):
        code = current_code(self.session_id)
        self.assertEqual(check_in(self.student_a, self.session_id, code=f'{code[:3]} {code[3:]}').status_code, 200)

    def test_bengali_and_other_script_digits_are_accepted(self):
        code = current_code(self.session_id)
        res = check_in(self.student_a, self.session_id, code=code.translate(BENGALI_DIGITS))  # QR path
        self.assertEqual(res.status_code, 200, res.data)
        bengali = code.translate(BENGALI_DIGITS)
        res = check_in(self.student_b, code=f'{bengali[:3]} {bengali[3:]}')  # typed, with a space
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(redis_service.get_submissions(self.session_id)['2302002']['method'], 'CODE')

    def test_wrong_code_in_other_digits_is_code_invalid(self):
        wrong = ('000000' if current_code(self.session_id) != '000000' else '111111').translate(BENGALI_DIGITS)
        fullwidth = '１２３４５６' if current_code(self.session_id) != '123456' else '６５４３２１'
        for res in (check_in(self.student_a, self.session_id, code=wrong),
                    check_in(self.student_a, code=wrong),
                    check_in(self.student_a, code=fullwidth),
                    check_in(self.student_a, self.session_id, code='১২৩৪৫²')):  # ² is not a decimal digit
            self.assertEqual((res.status_code, res.data['code']), (400, 'code_invalid'))
        self.assertEqual(redis_service.get_submissions(self.session_id), {})

    def test_course_of_a_deleted_semester(self):
        Semester.objects.filter(id=self.ci.semester_id).update(deleted=True)
        res = check_in(self.student_a, self.session_id)
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))
        res = check_in(self.student_a, code=current_code(self.session_id))
        self.assertEqual((res.status_code, res.data['code']), (400, 'code_invalid'))
        self.assertEqual(redis_service.get_submissions(self.session_id), {})

    def test_student_not_in_class(self):
        outsider = make_student('c@example.com', 2302003)
        res = check_in(outsider, self.session_id)
        self.assertEqual((res.status_code, res.data['code']), (403, 'not_enrolled'))
        # Typing another course's code finds nothing of theirs
        res = check_in(outsider, code=current_code(self.session_id))
        self.assertEqual((res.status_code, res.data['code']), (400, 'code_invalid'))

    def test_former_member_cannot_check_in(self):
        StudentClassroom.objects.filter(student=self.student_b).update(left_at='2026-01-01')
        res = check_in(self.student_b, self.session_id)
        self.assertEqual((res.status_code, res.data['code']), (403, 'not_enrolled'))

    def test_already_checked_in(self):
        self.assertEqual(check_in(self.student_a, self.session_id).status_code, 200)
        res = check_in(self.student_a, self.session_id, device_id='another-phone')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_checked_in'))

    def test_one_device_one_student_per_session(self):
        self.assertEqual(check_in(self.student_a, self.session_id, device_id='phone-1').status_code, 200)
        res = check_in(self.student_b, self.session_id, device_id='phone-1')
        self.assertEqual((res.status_code, res.data['code']), (409, 'device_used'))
        self.assertNotIn('2302002', redis_service.get_submissions(self.session_id))
        # The same phone works again in the next session (no permanent binding)
        self.teacher_client.post(f'/api/sessions/{self.session_id}/stop/')
        next_session = self.start()
        self.assertEqual(check_in(self.student_b, next_session, device_id='phone-1').status_code, 200)

    def test_device_id_is_required(self):
        res = client_for(self.student_a.user).post('/api/sessions/check-in/', {
            'code': current_code(self.session_id), 'session_id': self.session_id,
        }, format='json')
        self.assertEqual((res.status_code, res.data['message']), (400, 'Device ID is required.'))

    def test_after_the_timer_ends(self):
        expire_session(self.session_id)
        res = check_in(self.student_a, self.session_id)
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))

    def test_after_cancel(self):
        code = current_code(self.session_id)
        self.teacher_client.post(f'/api/sessions/{self.session_id}/cancel/')
        res = check_in(self.student_a, self.session_id, code=code)
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))

    def test_teachers_cannot_check_in(self):
        res = self.teacher_client.post('/api/sessions/check-in/', {
            'code': current_code(self.session_id), 'session_id': self.session_id, 'device_id': 'x',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_returns_503_when_too_busy(self):
        with mock.patch.object(redis_service, 'add_submission', side_effect=redis_service.SessionBusy):
            res = check_in(self.student_a, self.session_id)
        self.assertEqual(res.status_code, 503)

    def test_old_endpoints_are_gone(self):
        student = client_for(self.student_a.user)
        self.assertEqual(student.post(f'/api/sessions/{self.session_id}/checkin/', {}, format='json').status_code, 404)
        self.assertEqual(student.get(f'/api/sessions/course-info/{self.ci.id}/active/').status_code, 404)


class StatusTests(LiveTestCase):
    def test_live_status_lists_who_checked_in(self):
        session_id = self.start(delivery='ONLINE')
        check_in(self.student_a, session_id)
        res = self.teacher_client.get(f'/api/sessions/{session_id}/status/')
        self.assertTrue(res.data['active'])
        session = res.data['session']
        self.assertEqual((session['id'], session['delivery'], session['total']), (session_id, 'ONLINE', 2))
        self.assertGreater(session['time_left'], 0)
        [row] = session['checked_in']
        self.assertEqual(
            (row['profile_id'], row['student_id'], row['name'], row['method']),
            (str(self.student_a.id), 2302001, 'Ana Rahman', 'QR'),
        )
        self.assertIn('+06:00', row['time'])
        self.assertEqual(session['not_checked_in'],
                         [{'profile_id': str(self.student_b.id), 'student_id': 2302002, 'name': 'Student 2302002'}])

    def test_status_poll_after_timer_ends_saves_checkins(self):
        session_id = self.start()
        check_in(self.student_a, session_id)
        expire_session(session_id)

        res = self.teacher_client.get(f'/api/sessions/{session_id}/status/')
        self.assertEqual(res.data, {'success': True, 'active': False, 'session_id': session_id, 'saved': True,
                                    'total_present': 1})
        self.assertEqual(self.log_of(session_id, self.student_a).status, 'PRESENT')
        self.assertEqual(self.log_of(session_id, self.student_b).status, 'ABSENT')

        # The app still calls stop when its own timer hits zero: must not fail.
        res = self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AttendanceLog.objects.filter(session_id=session_id).count(), 2)

    def test_status_without_live_data_but_timer_running_stays_active(self):
        session_id = self.start()
        redis_service.delete_session_cache(session_id)
        res = self.teacher_client.get(f'/api/sessions/{session_id}/status/')
        self.assertTrue(res.data['active'])
        self.assertGreater(res.data['session']['time_left'], 0)
        self.assertEqual(res.data['session']['checked_in'], [])


class StopTests(LiveTestCase):
    def test_stop_saves_methods_and_times(self):
        session_id = self.start()
        check_in(self.student_a, code=current_code(session_id))
        with self.captureOnCommitCallbacks(execute=True):
            res = self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        self.assertEqual((res.status_code, res.data['total_present']), (200, 1))
        present = self.log_of(session_id, self.student_a)
        absent = self.log_of(session_id, self.student_b)
        self.assertEqual((present.status, present.method, present.source), ('PRESENT', 'CODE', 'QR_ONLINE'))
        self.assertEqual((absent.status, absent.method, absent.source), ('ABSENT', '', 'QR_ONLINE'))
        self.assertIsNotNone(present.time)
        self.assertIsNone(redis_service.get_session_cache(session_id))

    def test_stop_after_timer_ends_keeps_checkins(self):
        session_id = self.start()
        check_in(self.student_a, session_id)
        expire_session(session_id)
        self.assertEqual(self.teacher_client.post(f'/api/sessions/{session_id}/stop/').status_code, 200)
        self.assertEqual(self.log_of(session_id, self.student_a).status, 'PRESENT')
        self.assertEqual(self.log_of(session_id, self.student_b).status, 'ABSENT')

    def test_saving_twice_saves_once(self):
        session_id = self.start()
        session = AttendanceSession.objects.get(id=session_id)
        finalize_session(session)
        finalize_session(session)
        self.assertEqual(AttendanceLog.objects.filter(session=session).count(), 2)

    def test_old_live_data_without_methods_is_saved(self):
        """A session running during the upgrade has check-ins without method/time."""
        session_id = self.start()
        key = redis_service._key(session_id)
        import json
        data = json.loads(cache.get(key))
        data['submissions'] = {'2302001': {'name': 'Ana', 'mac': 'web01'}}
        del data['devices']
        cache.set(key, json.dumps(data), timeout=600)
        self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        log = self.log_of(session_id, self.student_a)
        self.assertEqual((log.status, log.method), ('PRESENT', 'QR'))


class ExtendTests(LiveTestCase):
    def test_extend_moves_the_end(self):
        session_id = self.start(duration_minutes=5)
        res = self.teacher_client.post(f'/api/sessions/{session_id}/extend/', {'minutes': 2}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(415 <= res.data['time_left'] <= 420)
        self.assertEqual(AttendanceSession.objects.get(id=session_id).duration_seconds, 420)
        self.assertTrue(415 <= redis_service.time_left(session_id) <= 420)
        status = self.teacher_client.get(f'/api/sessions/{session_id}/status/').data['session']
        self.assertEqual(status['ends_at'], res.data['ends_at'])

    def test_thirty_minutes_at_most(self):
        session_id = self.start(duration_minutes=15)
        for _ in range(7):
            self.assertEqual(
                self.teacher_client.post(f'/api/sessions/{session_id}/extend/', {'minutes': 2},
                                         format='json').status_code, 200,
            )
        res = self.teacher_client.post(f'/api/sessions/{session_id}/extend/', {'minutes': 2}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (400, 'too_long'))
        self.assertEqual(res.data['message'], 'A session can last at most 30 minutes. You can add up to 1 more minute.')
        self.assertEqual(AttendanceSession.objects.get(id=session_id).duration_seconds, 29 * 60)

    def test_cannot_extend_an_ended_session(self):
        session_id = self.start()
        expire_session(session_id)
        res = self.teacher_client.post(f'/api/sessions/{session_id}/extend/', {'minutes': 2}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))

    def test_check_ins_stay_open_after_extending(self):
        session_id = self.start()
        key = redis_service._key(session_id)
        import json
        data = json.loads(cache.get(key))
        data['end_time'] = time.time() + 1
        cache.set(key, json.dumps(data), timeout=600)
        self.teacher_client.post(f'/api/sessions/{session_id}/extend/', {'minutes': 2}, format='json')
        with mock.patch('time.time', return_value=time.time() + 30):
            self.assertTrue(redis_service.is_open(redis_service.get_session_cache(session_id)))


class LiveMarkTests(LiveTestCase):
    def test_teacher_mark_is_saved_with_the_session(self):
        session_id = self.start()
        res = self.teacher_client.post(f'/api/sessions/{session_id}/mark/', {'profile_id': str(self.student_b.id)},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['student']['student_id'], res.data['student']['method']), (2302002, 'TEACHER'))
        status = self.teacher_client.get(f'/api/sessions/{session_id}/status/').data['session']
        self.assertEqual([(r['student_id'], r['method']) for r in status['checked_in']], [(2302002, 'TEACHER')])

        self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        log = self.log_of(session_id, self.student_b)
        self.assertEqual((log.status, log.method, log.source), ('PRESENT', 'TEACHER', 'QR_ONLINE'))

    def test_marking_twice(self):
        session_id = self.start()
        check_in(self.student_a, session_id)
        res = self.teacher_client.post(f'/api/sessions/{session_id}/mark/', {'profile_id': str(self.student_a.id)},
                                       format='json')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_checked_in'))

    def test_rejects_student_from_another_class_or_a_former_member(self):
        session_id = self.start()
        outsider = make_student('c@example.com', 2302003)
        StudentClassroom.objects.filter(student=self.student_b).update(left_at='2026-01-01')
        for student in (outsider, self.student_b):
            res = self.teacher_client.post(f'/api/sessions/{session_id}/mark/', {'profile_id': str(student.id)},
                                           format='json')
            self.assertEqual((res.status_code, res.data['code']), (400, 'not_enrolled'))
        self.assertEqual(redis_service.get_submissions(session_id), {})

    def test_after_the_session_ended(self):
        session_id = self.start()
        expire_session(session_id)
        res = self.teacher_client.post(f'/api/sessions/{session_id}/mark/', {'profile_id': str(self.student_a.id)},
                                       format='json')
        self.assertEqual((res.status_code, res.data['code']), (410, 'session_ended'))


class CancelTests(LiveTestCase):
    def test_cancel_discards_everything(self):
        session_id = self.start()
        check_in(self.student_a, session_id)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.teacher_client.post(f'/api/sessions/{session_id}/cancel/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(AttendanceSession.objects.exists())
        self.assertFalse(AttendanceLog.objects.exists())
        self.assertIsNone(redis_service.get_session_cache(session_id))
        self.assertEqual(self.teacher_client.get(f'/api/sessions/{session_id}/status/').status_code, 404)

    def test_a_saved_session_cannot_be_cancelled(self):
        session_id = self.start()
        self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        res = self.teacher_client.post(f'/api/sessions/{session_id}/cancel/')
        self.assertEqual((res.status_code, res.data['code']), (409, 'session_saved'))
        self.assertEqual(AttendanceLog.objects.filter(session_id=session_id).count(), 2)

    def test_saving_a_cancelled_session_does_nothing(self):
        session_id = self.start()
        session = AttendanceSession.objects.get(id=session_id)
        self.teacher_client.post(f'/api/sessions/{session_id}/cancel/')
        self.assertIsNone(finalize_session(session))
        self.assertFalse(AttendanceLog.objects.exists())


class TeacherLiveLookupTests(LiveTestCase):
    def test_reopen_after_a_reload(self):
        url = f'/api/teacher/course-info/{self.ci.id}/live/'
        self.assertEqual(self.teacher_client.get(url).data, {'success': True, 'session': None})
        session_id = self.start(delivery='ONLINE')
        session = self.teacher_client.get(url).data['session']
        self.assertEqual((session['id'], session['delivery'], session['code_period']), (session_id, 'ONLINE', 30))
        expire_session(session_id)
        self.assertIsNone(self.teacher_client.get(url).data['session'])
        self.assertFalse(AttendanceSession.objects.get(id=session_id).is_active)  # saved on the way

    def test_other_teacher(self):
        other = client_for(make_teacher('other@example.com').user)
        self.assertEqual(other.get(f'/api/teacher/course-info/{self.ci.id}/live/').status_code, 403)


class StudentLiveListTests(LiveTestCase):
    def test_live_now(self):
        student = client_for(self.student_a.user)
        self.assertEqual(student.get('/api/student/live/').data, {'success': True, 'sessions': []})
        session_id = self.start(delivery='ONLINE')

        [row] = student.get('/api/student/live/').data['sessions']
        self.assertEqual(
            {k: row[k] for k in ('session_id', 'course_info_id', 'course', 'delivery', 'checked_in')},
            {'session_id': session_id, 'course_info_id': str(self.ci.id),
             'course': {'code': 'CSE301', 'title': 'Course CSE301'}, 'delivery': 'ONLINE', 'checked_in': False},
        )
        self.assertNotIn('code', row)
        self.assertNotIn('qr_token', row)
        check_in(self.student_a, session_id)
        self.assertTrue(student.get('/api/student/live/').data['sessions'][0]['checked_in'])

        expire_session(session_id)
        self.assertEqual(student.get('/api/student/live/').data['sessions'], [])

    def test_only_their_own_current_courses(self):
        self.start()
        outsider = make_student('c@example.com', 2302003)
        self.assertEqual(client_for(outsider.user).get('/api/student/live/').data['sessions'], [])
        StudentClassroom.objects.filter(student=self.student_b).update(left_at='2026-01-01')
        self.assertEqual(client_for(self.student_b.user).get('/api/student/live/').data['sessions'], [])

    def test_teachers_cannot_use_it(self):
        self.assertEqual(self.teacher_client.get('/api/student/live/').status_code, 403)


class FingerprintSessionTests(LiveTestCase):
    def test_fingerprint_logs_use_hardware_source(self):
        session_id = self.start(mode='FINGERPRINT')
        result = redis_service.add_submission(session_id, self.student_a.student_id, 'Ana', 'FINGERPRINT')
        self.assertEqual(result, redis_service.ADDED)
        self.teacher_client.post(f'/api/sessions/{session_id}/stop/')
        log = self.log_of(session_id, self.student_a)
        self.assertEqual((log.status, log.source, log.method), ('PRESENT', 'HARDWARE', 'FINGERPRINT'))


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
        res = start_session(self.other_client, self.ci)
        self.assertEqual(res.status_code, 403)
        self.assertEqual((res.data['code'], res.data['message']), ('permission_denied', 'You do not teach this course.'))
        self.assertFalse(AttendanceSession.objects.exists())

    def test_other_teacher_cannot_control_owners_session(self):
        session_id = start_session(self.owner_client, self.ci).data['session']['id']

        for path in ('code', 'status'):
            self.assertEqual(self.other_client.get(f'/api/sessions/{session_id}/{path}/').status_code, 403)
        for path, body in (('mark', {'profile_id': str(self.student.id)}), ('extend', {'minutes': 2}),
                           ('stop', {}), ('cancel', {})):
            res = self.other_client.post(f'/api/sessions/{session_id}/{path}/', body, format='json')
            self.assertEqual(res.status_code, 403, path)
        self.assertTrue(AttendanceSession.objects.get(id=session_id).is_active)
        self.assertEqual(redis_service.get_submissions(session_id), {})

    def test_other_teacher_cannot_read_or_edit_course(self):
        ci = self.ci.id
        base = f'/api/teacher/course-info/{ci}'
        self.assertEqual(self.other_client.get(f'{base}/').status_code, 403)
        self.assertEqual(self.other_client.get(f'{base}/students/{self.student.id}/').status_code, 403)
        self.assertEqual(self.other_client.get(f'/api/sessions/course-info/{ci}/history/').status_code, 403)
        self.assertEqual(self.other_client.post(f'{base}/roll-call/', {
            'date': '2026-10-01', 'present_profile_ids': [],
        }, format='json').status_code, 403)
        self.assertEqual(self.other_client.put(f'{base}/attendance/', {
            'date': '2026-10-01', 'profile_id': str(self.student.id), 'status': 'PRESENT',
        }, format='json').status_code, 403)
        self.assertEqual(self.other_client.delete(f'{base}/history-session/2026-10-01/').status_code, 403)
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
            threading.Thread(
                target=redis_service.add_submission,
                args=(session_id, 2302000 + i, 'S', 'QR', f'device-{i}', str(uuid.uuid4())),
            )
            for i in range(40)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = redis_service.get_session_cache(session_id)
        self.assertEqual(len(data['submissions']), 40)
        self.assertEqual(len(data['devices']), 40)


class SessionEdgeCaseTests(LiveTestCase):
    """Cases found in review: start races, busy locks, Redis trouble."""

    def test_lookup_while_session_has_no_live_data_yet_keeps_it_running(self):
        session = AttendanceSession.objects.create(
            course_info=self.ci, date='2026-10-05', mode='QR_ONLINE', duration_seconds=60, qr_token='t',
        )
        res = client_for(self.student_a.user).get('/api/student/live/')
        self.assertEqual([r['session_id'] for r in res.data['sessions']], [str(session.id)])
        session.refresh_from_db()
        self.assertTrue(session.is_active)
        self.assertFalse(AttendanceLog.objects.exists())

    def test_course_pages_still_load_when_redis_fails(self):
        session_id = self.start()
        with mock.patch.object(redis_service, 'get_session_cache', side_effect=ConnectionError('redis down')), \
                self.assertLogs('apps.attendance.services', level='ERROR'):
            res = self.teacher_client.get(f'/api/teacher/course-info/{self.ci.id}/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data['course']['live_session_id'], session_id)  # the database timer runs
            res = client_for(self.student_a.user).get('/api/student/live/')
            self.assertEqual(res.status_code, 200)
        self.assertTrue(AttendanceSession.objects.get(id=session_id).is_active)

    def test_opening_the_teacher_dashboard_saves_ended_sessions(self):
        session_id = self.start()
        check_in(self.student_a, session_id)
        expire_session(session_id)

        res = self.teacher_client.get(f'/api/teacher/{self.teacher.user.id}/courses/')

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['current'][0]['live_session_id'])
        self.assertFalse(AttendanceSession.objects.get(id=session_id).is_active)
        self.assertEqual(AttendanceLog.objects.get(student=self.student_a).status, 'PRESENT')


class CourseLockTests(TestCase):
    def test_locks_only_the_course_info_row(self):
        ci = make_course_info(make_teacher('t@example.com'))
        with CaptureQueriesContext(connection) as queries:
            _lock_course(ci)
        [query] = queries.captured_queries
        # CourseInfo's Meta ordering would join (and on Postgres lock) the semester and course rows
        self.assertNotIn('JOIN', query['sql'])
