"""Teacher: courses, students, history and corrections (API v2 section 6)."""
import datetime

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academic.models import StudentClassroom
from apps.attendance.models import AttendanceChange, AttendanceLog, AttendanceSession
from apps.attendance.tests.helpers import (
    client_for, make_admin, make_course_info, make_semester, make_student, make_teacher, start_session,
)
from apps.faces.models import StudentFace

D1, D2, D3, D4 = (datetime.date(2026, 9, d) for d in (1, 2, 3, 4))


class TeacherTestCase(TestCase):
    """a, b from the start; `late` joined on D3. Classes on D1, D2 (QR session) and D3, D4."""

    def setUp(self):
        cache.clear()
        self.teacher = make_teacher('t@example.com', first_name='Tariq', last_name='Islam')
        self.a = make_student('a@example.com', 2302001, first_name='Ana', last_name='Rahman')
        self.b = make_student('b@example.com', 2302002)
        self.late = make_student('late@example.com', 2302003)
        self.semester = make_semester(students=[self.a, self.b, self.late])
        StudentClassroom.objects.filter(student=self.late).update(joined_at=D3)
        self.ci = make_course_info(self.teacher, semester=self.semester)
        self.client = client_for(self.teacher.user)
        self.qr = AttendanceSession.objects.create(
            course_info=self.ci, date=D2, mode='QR_ONLINE', is_active=False, delivery='ONLINE',
        )
        for student, day, status, method, session in [
            (self.a, D1, 'PRESENT', 'TEACHER', None), (self.b, D1, 'ABSENT', 'TEACHER', None),
            (self.a, D2, 'PRESENT', 'QR', self.qr), (self.b, D2, 'ABSENT', '', self.qr),
            (self.a, D3, 'ABSENT', 'TEACHER', None), (self.b, D3, 'ABSENT', 'TEACHER', None),
            (self.late, D3, 'PRESENT', 'TEACHER', None),
            (self.a, D4, 'PRESENT', 'TEACHER', None), (self.b, D4, 'ABSENT', 'TEACHER', None),
            (self.late, D4, 'PRESENT', 'TEACHER', None),
        ]:
            AttendanceLog.objects.create(
                course_info=self.ci, student=student, date=day, status=status, method=method, session=session,
                source='QR_ONLINE' if session else 'MANUAL',
            )

    def url(self, rest=''):
        return f'/api/teacher/course-info/{self.ci.id}/{rest}'


class CourseListTests(TeacherTestCase):
    def test_current_and_previous_with_numbers(self):
        StudentFace.objects.create(student=self.a, pose='STRAIGHT', crop_jpeg=b'x', embedding=b'x', engine='fake',
                                   consented_at=timezone.now())
        old_semester = make_semester('Second', 'II', is_active=False)
        old = make_course_info(self.teacher, 'CSE201', semester=old_semester)
        live_id = start_session(self.client, self.ci).data['session']['id']

        res = client_for(self.teacher.user).get(f'/api/teacher/{self.teacher.id}/courses/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([c['course_info_id'] for c in res.data['previous']], [str(old.id)])
        [course] = res.data['current']
        self.assertEqual(course, {
            'course_info_id': str(self.ci.id), 'code': 'CSE301', 'title': 'Course CSE301',
            'credits': 'CREDIT_3_00',
            'semester': {'id': str(self.semester.id), 'label': 'Level 3 · Term I · 2025-26', 'is_active': True},
            'student_count': 3, 'classes_held': 4,
            # a 3/4 = 75, b 0/4 = 0, late 2/2 = 100 -> mean 58.3; b is below 75
            'average_percent': 58.3, 'below_min_count': 1, 'face_registered_count': 1,
            'live_session_id': live_id,
        })
        # Also by user id, and for admins
        res = client_for(make_admin('admin@example.com')).get(f'/api/teacher/{self.teacher.user.id}/courses/')
        self.assertEqual(len(res.data['current']), 1)

    @override_settings(ATTENDANCE_MIN_PERCENT=80)
    def test_minimum_comes_from_settings(self):
        res = self.client.get(f'/api/teacher/{self.teacher.id}/courses/')
        self.assertEqual(res.data['current'][0]['below_min_count'], 2)  # a is at 75 now


class CourseDetailTests(TeacherTestCase):
    def test_students_counted_from_their_join_date(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['course']['course_info_id'], str(self.ci.id))
        rows = {s['student_id']: s for s in res.data['students']}
        self.assertEqual(list(rows), [2302001, 2302002, 2302003])
        self.assertEqual({k: rows[2302003][k] for k in ('joined_at', 'left_at', 'attended', 'held', 'percent',
                                                         'below_min', 'face_registered')},
                         {'joined_at': '2026-09-03', 'left_at': None, 'attended': 2, 'held': 2, 'percent': 100.0,
                          'below_min': False, 'face_registered': False})
        self.assertEqual((rows[2302001]['percent'], rows[2302001]['below_min']), (75.0, False))
        self.assertEqual((rows[2302002]['percent'], rows[2302002]['below_min']), (0.0, True))
        self.assertEqual(res.data['dates'], [
            {'date': '2026-09-04', 'present': 2, 'total': 3},
            {'date': '2026-09-03', 'present': 1, 'total': 3},
            {'date': '2026-09-02', 'present': 1, 'total': 2},
            {'date': '2026-09-01', 'present': 1, 'total': 2},
        ])

    def test_no_classes_means_no_percent(self):
        AttendanceLog.objects.all().delete()
        res = self.client.get(self.url())
        self.assertEqual({(s['held'], s['percent'], s['below_min']) for s in res.data['students']},
                         {(0, None, False)})
        self.assertIsNone(res.data['course']['average_percent'])


class StudentDaysTests(TeacherTestCase):
    def test_days_before_joining_are_null(self):
        res = self.client.get(self.url(f'students/{self.late.id}/'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['student'], {
            'profile_id': str(self.late.id), 'student_id': 2302003, 'name': 'Student 2302003',
            'email': 'late@example.com', 'joined_at': '2026-09-03', 'left_at': None,
            'attended': 2, 'held': 2, 'percent': 100.0,
        })
        self.assertEqual([(d['date'], d['status'], d['method']) for d in res.data['days']], [
            ('2026-09-04', 'PRESENT', 'TEACHER'), ('2026-09-03', 'PRESENT', 'TEACHER'),
            ('2026-09-02', None, None), ('2026-09-01', None, None),
        ])

    def test_a_class_date_without_a_log_is_absent(self):
        AttendanceLog.objects.filter(student=self.a, date=D2).delete()
        days = self.client.get(self.url(f'students/{self.a.id}/')).data['days']
        self.assertEqual(days[2], {'date': '2026-09-02', 'status': 'ABSENT', 'method': None, 'changed_by': None,
                                   'changed_at': None})

    def test_unknown_student(self):
        outsider = make_student('x@example.com', 2309999)
        self.assertEqual(self.client.get(self.url(f'students/{outsider.id}/')).status_code, 404)


class HistoryTests(TeacherTestCase):
    def test_days_with_sessions_and_one_row_per_student(self):
        res = self.client.get(f'/api/sessions/course-info/{self.ci.id}/history/')
        self.assertEqual(res.status_code, 200)
        history = res.data['history']
        self.assertEqual([d['date'] for d in history], ['2026-09-04', '2026-09-03', '2026-09-02', '2026-09-01'])
        day2 = history[2]
        self.assertEqual(day2['sessions'], [{'session_id': str(self.qr.id), 'delivery': 'ONLINE', 'mode': 'QR_ONLINE'}])
        self.assertEqual(day2['logs'], [
            {'profile_id': str(self.a.id), 'student_id': 2302001, 'name': 'Ana Rahman', 'status': 'PRESENT',
             'method': 'QR', 'changed_by': None, 'changed_at': None},
            {'profile_id': str(self.b.id), 'student_id': 2302002, 'name': 'Student 2302002', 'status': 'ABSENT',
             'method': None, 'changed_by': None, 'changed_at': None},
        ])
        self.assertEqual(history[3]['sessions'], [])  # a roll call

    def test_filters(self):
        res = self.client.get(f'/api/sessions/course-info/{self.ci.id}/history/?date=2026-09-03')
        self.assertEqual([d['date'] for d in res.data['history']], ['2026-09-03'])
        res = self.client.get(f'/api/sessions/course-info/{self.ci.id}/history/?student_id={self.late.id}')
        self.assertEqual([d['date'] for d in res.data['history']], ['2026-09-04', '2026-09-03'])
        self.assertEqual(self.client.get(f'/api/sessions/course-info/{self.ci.id}/history/?date=soon').status_code,
                         400)
        self.assertEqual(
            self.client.get(f'/api/sessions/course-info/{self.ci.id}/history/?date=2026-02-30').status_code, 400,
        )


class CorrectionTests(TeacherTestCase):
    def correct(self, student, day, status, client=None):
        return (client or self.client).put(self.url('attendance/'), {
            'date': day.isoformat(), 'profile_id': str(student.id), 'status': status,
        }, format='json')

    def test_changes_only_that_student_and_keeps_the_method(self):
        res = self.correct(self.b, D2, 'PRESENT')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['changed'])
        self.assertEqual((res.data['day']['status'], res.data['day']['changed_by']), ('PRESENT', 'Tariq Islam'))
        log = AttendanceLog.objects.get(student=self.b, date=D2)
        self.assertEqual((log.status, log.source, log.session_id, log.changed_by_id),
                         ('PRESENT', 'QR_ONLINE', self.qr.id, self.teacher.user.id))
        self.assertIsNotNone(log.changed_at)
        change = AttendanceChange.objects.get()
        self.assertEqual((change.log_id, change.old_status, change.new_status, change.changed_by_id),
                         (log.id, 'ABSENT', 'PRESENT', self.teacher.user.id))
        self.assertEqual(AttendanceLog.objects.get(student=self.a, date=D2).changed_at, None)

        day = self.client.get(self.url(f'students/{self.b.id}/')).data['days'][2]
        self.assertEqual((day['status'], day['changed_by']), ('PRESENT', 'Tariq Islam'))

    def test_same_status_changes_nothing(self):
        res = self.correct(self.a, D1, 'PRESENT')
        self.assertEqual((res.status_code, res.data['changed']), (200, False))
        self.assertFalse(AttendanceChange.objects.exists())

    def test_absent_on_a_day_with_several_present_logs(self):
        face = AttendanceSession.objects.create(course_info=self.ci, date=D2, mode='FACE', is_active=False)
        AttendanceLog.objects.create(course_info=self.ci, student=self.a, date=D2, status='PRESENT', method='FACE',
                                     source='FACE', session=face)
        self.correct(self.a, D2, 'ABSENT')
        self.assertEqual(set(AttendanceLog.objects.filter(student=self.a, date=D2).values_list('status', 'method')),
                         {('ABSENT', 'QR'), ('ABSENT', 'FACE')})
        self.assertEqual(AttendanceChange.objects.count(), 2)

    def test_a_missing_log_is_created(self):
        AttendanceLog.objects.filter(student=self.b, date=D1).delete()
        self.assertTrue(self.correct(self.b, D1, 'PRESENT').data['changed'])
        log = AttendanceLog.objects.get(student=self.b, date=D1)
        self.assertEqual((log.status, log.method, log.source), ('PRESENT', 'TEACHER', 'MANUAL'))
        self.assertEqual(AttendanceChange.objects.get().old_status, '')

    def test_errors(self):
        res = self.correct(self.a, datetime.date(2026, 9, 10), 'PRESENT')
        self.assertEqual((res.status_code, res.data['code']), (404, 'no_class_on_date'))
        res = self.correct(self.late, D1, 'PRESENT')  # joined on D3
        self.assertEqual((res.status_code, res.data['code']), (400, 'not_enrolled'))
        res = self.correct(self.a, timezone.localdate() + datetime.timedelta(days=1), 'PRESENT')
        self.assertEqual((res.status_code, res.data['code']), (400, 'future_date'))
        res = self.correct(self.a, D1, 'LATE')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(AttendanceChange.objects.exists())

    def test_admin_may_correct(self):
        res = self.correct(self.b, D1, 'PRESENT', client=client_for(make_admin('admin@example.com')))
        self.assertEqual(res.status_code, 200)


class RollCallTests(TeacherTestCase):
    def roll_call(self, day, present):
        return self.client.post(self.url('roll-call/'), {
            'date': day.isoformat(), 'present_profile_ids': [str(s.id) for s in present],
        }, format='json')

    def test_creates_the_class(self):
        day = datetime.date(2026, 9, 7)
        res = self.roll_call(day, [self.a, self.late])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual({k: res.data[k] for k in ('date', 'present', 'absent', 'changed')},
                         {'date': '2026-09-07', 'present': 2, 'absent': 1, 'changed': 0})
        logs = AttendanceLog.objects.filter(date=day)
        self.assertEqual(set(logs.values_list('student__student_id', 'status', 'source', 'method')), {
            (2302001, 'PRESENT', 'MANUAL', 'TEACHER'), (2302002, 'ABSENT', 'MANUAL', 'TEACHER'),
            (2302003, 'PRESENT', 'MANUAL', 'TEACHER'),
        })
        self.assertFalse(AttendanceChange.objects.exists())

    def test_never_relabels_other_methods(self):
        res = self.roll_call(D2, [self.a, self.b])  # b was absent in the QR session
        self.assertEqual((res.data['present'], res.data['absent'], res.data['changed']), (2, 0, 1))
        logs = {log.student_id: log for log in AttendanceLog.objects.filter(date=D2)}
        self.assertEqual(len(logs), 2)  # no new logs; late was not enrolled on D2
        self.assertEqual((logs[self.a.id].method, logs[self.a.id].changed_at), ('QR', None))  # unchanged
        self.assertEqual((logs[self.b.id].status, logs[self.b.id].source, logs[self.b.id].session_id),
                         ('PRESENT', 'QR_ONLINE', self.qr.id))
        self.assertEqual(logs[self.b.id].changed_by_id, self.teacher.user.id)
        change = AttendanceChange.objects.get()
        self.assertEqual((change.old_status, change.new_status), ('ABSENT', 'PRESENT'))

    def test_saving_the_same_roll_call_twice_changes_nothing(self):
        self.roll_call(D4, [self.a, self.late])
        self.assertEqual(self.roll_call(D4, [self.a, self.late]).data['changed'], 0)
        self.assertEqual(AttendanceLog.objects.filter(date=D4).count(), 3)

    def test_future_date(self):
        res = self.roll_call(timezone.localdate() + datetime.timedelta(days=1), [])
        self.assertEqual((res.status_code, res.data['code']), (400, 'future_date'))
        self.assertEqual(self.client.post(self.url('roll-call/'), {'date': '2026-09-07'}, format='json').status_code,
                         400)


class DeleteDateTests(TeacherTestCase):
    def test_deletes_the_dates_classes_and_logs(self):
        res = self.client.delete(self.url('history-session/2026-09-02/'))
        self.assertEqual((res.status_code, res.data['deleted']), (200, 2))
        self.assertFalse(AttendanceLog.objects.filter(date=D2).exists())
        self.assertFalse(AttendanceSession.objects.exists())
        self.assertEqual(AttendanceLog.objects.count(), 8)

    def test_not_while_a_session_is_live_that_day(self):
        start_session(self.client, self.ci)
        today = timezone.localdate().isoformat()
        res = self.client.delete(self.url(f'history-session/{today}/'))
        self.assertEqual((res.status_code, res.data['code']), (409, 'session_running'))

    def test_bad_date(self):
        self.assertEqual(self.client.delete(self.url('history-session/someday/')).status_code, 400)
