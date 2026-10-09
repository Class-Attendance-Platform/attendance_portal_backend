"""Student: semesters summary and one course's days (API v2 section 7)."""
import datetime
import importlib

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.academic.models import Classroom, StudentClassroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.records import classes_needed
from apps.attendance.tests.helpers import (
    check_in, client_for, expire_session, make_course_info, make_semester, make_student, make_teacher,
    start_session,
)

D = [datetime.date(2026, 9, d) for d in range(1, 6)]


class StudentTestCase(TestCase):
    def setUp(self):
        self.teacher = make_teacher('t@example.com', first_name='Tariq', last_name='Islam')
        self.student = make_student('s@example.com', 2302001)
        self.other = make_student('o@example.com', 2302002)
        # Created out of order on purpose: the list is sorted by level, then term
        self.third = make_semester('Third', 'I', students=[self.student, self.other])
        self.second = make_semester('Second', 'II', is_active=False, students=[self.student])
        StudentClassroom.objects.filter(student=self.student, classroom__semester=self.second).update(left_at=D[0])
        self.ci = make_course_info(self.teacher, 'CSE301', semester=self.third)
        self.other_ci = make_course_info(self.teacher, 'CSE303', semester=self.third)
        # Class dates D1..D5; the student joined on D3: present D3, absent D4, no log on D5
        for day in D:
            AttendanceLog.objects.create(course_info=self.ci, student=self.other, date=day, status='PRESENT')
        StudentClassroom.objects.filter(student=self.student, classroom__semester=self.third).update(joined_at=D[2])
        AttendanceLog.objects.create(course_info=self.ci, student=self.student, date=D[2], status='PRESENT',
                                     method='QR')
        AttendanceLog.objects.create(course_info=self.ci, student=self.student, date=D[3], status='ABSENT')
        self.client = client_for(self.student.user)


class SemestersTests(StudentTestCase):
    def test_sorted_by_level_then_term_with_membership(self):
        res = self.client.get(f'/api/student/{self.student.id}/semesters/')
        self.assertEqual(res.status_code, 200)
        semesters = res.data['semesters']
        self.assertEqual([s['label'] for s in semesters], ['Level 2 · Term II · 2025-26', 'Level 3 · Term I · 2025-26'])
        second, third = semesters
        self.assertEqual((second['is_active'], second['left_at'], second['overall_percent']), (False, '2026-09-01', None))
        self.assertEqual((third['is_active'], third['joined_at'], third['left_at']), (True, '2026-09-03', None))

    def test_numbers_count_from_the_join_date(self):
        third = client_for(self.student.user).get(f'/api/student/{self.student.user.id}/semesters/').data['semesters'][1]
        course, untaught = third['courses']
        self.assertEqual(course['course_info_id'], str(self.ci.id))
        # held = D3, D4, D5 (no log on D5 = absent); attended = D3
        self.assertEqual({k: course[k] for k in ('held', 'attended', 'percent', 'below_min', 'classes_needed')},
                         {'held': 3, 'attended': 1, 'percent': 33.3, 'below_min': True, 'classes_needed': 5})
        self.assertEqual((course['totalClasses'], course['presentCount'], course['percentage']), (3, 1, 33.33))
        self.assertEqual(course['history'], [
            {'date': '2026-09-03', 'present': True}, {'date': '2026-09-04', 'present': False},
            {'date': '2026-09-05', 'present': False},
        ])
        self.assertEqual({k: untaught[k] for k in ('held', 'percent', 'below_min', 'classes_needed')},
                         {'held': 0, 'percent': None, 'below_min': False, 'classes_needed': 0})
        self.assertEqual(third['overall_percent'], 33.3)

    def test_only_their_own_class_group(self):
        other_group = Classroom.objects.create(name='B', semester=self.third)
        elsewhere = make_course_info(self.teacher, 'CSE399', semester=self.third)
        elsewhere.classroom = other_group
        elsewhere.save()
        third = self.client.get(f'/api/student/{self.student.id}/semesters/').data['semesters'][1]
        self.assertEqual([c['course']['code'] for c in third['courses']], ['CSE301', 'CSE303'])

    def test_not_someone_elses(self):
        res = self.client.get(f'/api/student/{self.other.id}/semesters/')
        self.assertEqual((res.status_code, res.data['code']), (403, 'permission_denied'))

    @override_settings(ATTENDANCE_MIN_PERCENT=30)
    def test_minimum_comes_from_settings(self):
        third = self.client.get(f'/api/student/{self.student.id}/semesters/').data['semesters'][1]
        self.assertEqual((third['courses'][0]['below_min'], third['courses'][0]['classes_needed']), (False, 0))


class ClassesNeededTests(TestCase):
    def test_classes_needed(self):
        self.assertEqual(classes_needed(0, 0), 0)
        self.assertEqual(classes_needed(3, 4), 0)       # 75%
        self.assertEqual(classes_needed(5, 10), 10)     # 15/20 = 75%
        self.assertEqual(classes_needed(1, 3), 5)       # 6/8 = 75%
        self.assertEqual(classes_needed(2, 3, minimum=100), None)
        self.assertEqual(classes_needed(3, 3, minimum=100), 0)


class StudentCourseDetailTests(StudentTestCase):
    def test_own_course(self):
        AttendanceLog.objects.filter(student=self.student, date=D[3]).update(changed_at=datetime.datetime(
            2026, 9, 6, tzinfo=datetime.timezone.utc))
        res = self.client.get(f'/api/student/course-info/{self.ci.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['course'], {
            'course_info_id': str(self.ci.id), 'code': 'CSE301', 'title': 'Course CSE301', 'credits': 'CREDIT_3_00',
            'teacher_name': 'Tariq Islam',
            'semester': {'label': 'Level 3 · Term I · 2025-26', 'is_active': True,
                         'start_date': None, 'end_date': None},
        })
        self.assertEqual({k: res.data[k] for k in ('attended', 'held', 'percent', 'classes_needed')},
                         {'attended': 1, 'held': 3, 'percent': 33.3, 'classes_needed': 5})
        self.assertEqual((res.data['joined_at'], res.data['left_at']), ('2026-09-03', None))
        self.assertEqual(res.data['days'], [
            {'date': '2026-09-05', 'status': 'ABSENT', 'method': None, 'changed': False,
             'absent_reason': 'not_checked_in'},  # a class date without a log for them
            {'date': '2026-09-04', 'status': 'ABSENT', 'method': None, 'changed': True, 'absent_reason': 'changed'},
            {'date': '2026-09-03', 'status': 'PRESENT', 'method': 'QR', 'changed': False, 'absent_reason': None},
            {'date': '2026-09-02', 'status': None, 'method': None, 'changed': False, 'absent_reason': None},
            {'date': '2026-09-01', 'status': None, 'method': None, 'changed': False, 'absent_reason': None},
        ])

    def test_not_theirs(self):
        stranger = make_student('x@example.com', 2309999)
        res = client_for(stranger.user).get(f'/api/student/course-info/{self.ci.id}/')
        self.assertEqual((res.status_code, res.data['code']), (403, 'not_enrolled'))
        self.assertEqual(client_for(self.teacher.user).get(f'/api/student/course-info/{self.ci.id}/').status_code, 403)

    def test_former_member_still_sees_the_history(self):
        StudentClassroom.objects.filter(student=self.student, classroom__semester=self.third).update(left_at=D[4])
        res = self.client.get(f'/api/student/course-info/{self.ci.id}/')
        self.assertEqual((res.data['held'], res.data['days'][0]['status']), (2, None))
        self.assertEqual(res.data['left_at'], '2026-09-05')

    def test_a_student_added_after_classes_were_held(self):
        newbie = make_student('n@example.com', 2302003)
        StudentClassroom.objects.create(student=newbie, classroom=self.ci.classroom, joined_at=D[4] + datetime.timedelta(1))
        res = client_for(newbie.user).get(f'/api/student/course-info/{self.ci.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data['joined_at'], res.data['left_at'], res.data['held']), ('2026-09-06', None, 0))
        self.assertEqual({d['status'] for d in res.data['days']}, {None})  # every class was before they joined


class AbsentReasonTests(TestCase):
    """GET /student/course-info/<id>/: why each absent day is absent (absent_reason)."""

    def setUp(self):
        self.teacher = make_teacher('t@example.com', first_name='Tariq', last_name='Islam')
        self.student = make_student('s@example.com', 2302001)
        self.ci = make_course_info(self.teacher, students=[self.student])
        self.teacher_client = client_for(self.teacher.user)

    def session(self, day, mode):
        return AttendanceSession.objects.create(course_info=self.ci, date=day, mode=mode, is_active=False)

    def add_log(self, day, status, source, method='', session=None, time=None):
        return AttendanceLog.objects.create(course_info=self.ci, student=self.student, date=day, status=status,
                                            source=source, method=method, session=session, time=time)

    def correct(self, day, status):
        res = self.teacher_client.put(f'/api/teacher/course-info/{self.ci.id}/attendance/', {
            'date': day.isoformat(), 'profile_id': str(self.student.id), 'status': status,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_each_kind_of_absent_day(self):
        days = [datetime.date(2026, 9, d) for d in range(1, 8)]
        self.add_log(days[0], 'ABSENT', 'MANUAL', 'TEACHER')                                     # roll call
        self.add_log(days[1], 'ABSENT', 'QR_ONLINE', session=self.session(days[1], 'QR_ONLINE'))  # live session
        self.add_log(days[2], 'ABSENT', 'FACE', session=self.session(days[2], 'FACE'))            # class photo
        self.add_log(days[3], 'PRESENT', 'QR_ONLINE', 'QR', session=self.session(days[3], 'QR_ONLINE'))
        self.correct(days[3], 'ABSENT')  # checked in by QR, then the teacher changed it
        # Two sessions: not in the class photo (09:00), checked in by QR (10:00), then changed to absent
        self.add_log(days[4], 'ABSENT', 'FACE', session=self.session(days[4], 'FACE'), time=datetime.time(9))
        self.add_log(days[4], 'PRESENT', 'QR_ONLINE', 'QR', session=self.session(days[4], 'QR_ONLINE'),
                     time=datetime.time(10))
        self.correct(days[4], 'ABSENT')
        self.add_log(days[5], 'PRESENT', 'MANUAL', 'TEACHER')
        # days[6]: a class with a log for someone else only
        other = make_student('o@example.com', 2302002)
        StudentClassroom.objects.create(student=other, classroom=self.ci.classroom)
        AttendanceLog.objects.create(course_info=self.ci, student=other, date=days[6], status='PRESENT')

        res = client_for(self.student.user).get(f'/api/student/course-info/{self.ci.id}/')
        self.assertEqual({d['date']: (d['status'], d['absent_reason'], d['changed']) for d in res.data['days']}, {
            '2026-09-07': ('ABSENT', 'not_checked_in', False),
            '2026-09-06': ('PRESENT', None, False),
            '2026-09-05': ('ABSENT', 'changed', True),
            '2026-09-04': ('ABSENT', 'changed', True),
            '2026-09-03': ('ABSENT', 'class_photo', False),
            '2026-09-02': ('ABSENT', 'not_checked_in', False),
            '2026-09-01': ('ABSENT', 'roll_call', False),
        })
        # The teacher's pages show that correction for the two-session day too
        history = self.teacher_client.get(f'/api/sessions/course-info/{self.ci.id}/history/?date=2026-09-05')
        self.assertEqual(history.data['history'][0]['logs'][0]['changed_by'], 'Tariq Islam')
        teacher_days = self.teacher_client.get(f'/api/teacher/course-info/{self.ci.id}/students/{self.student.id}/')
        self.assertNotIn('absent_reason', teacher_days.data['days'][0])  # only the student's page has it


class SummarySavesEndedSessionsTests(TestCase):
    def test_a_session_past_its_timer_is_counted(self):
        cache.clear()
        teacher = make_teacher('t@example.com')
        came = make_student('s@example.com', 2302001)
        missed = make_student('m@example.com', 2302002)
        ci = make_course_info(teacher, students=[came, missed])
        session_id = start_session(client_for(teacher.user), ci).data['session']['id']
        self.assertEqual(check_in(came, session_id).status_code, 200)
        expire_session(session_id)  # nobody stopped it and no teacher screen saved it

        [semester] = client_for(missed.user).get(f'/api/student/{missed.id}/semesters/').data['semesters']
        course = semester['courses'][0]
        self.assertEqual((course['held'], course['attended'], course['percent'], course['below_min']),
                         (1, 0, 0.0, True))
        self.assertFalse(AttendanceSession.objects.get(id=session_id).is_active)
        [semester] = client_for(came.user).get(f'/api/student/{came.id}/semesters/').data['semesters']
        self.assertEqual((semester['courses'][0]['attended'], semester['overall_percent']), (1, 100.0))


class FillMethodMigrationTests(TestCase):
    def test_old_logs_get_a_method(self):
        migration = importlib.import_module('apps.attendance.migrations.0004_fill_log_method')
        teacher = make_teacher('t@example.com')
        student = make_student('s@example.com', 2302001)
        ci = make_course_info(teacher, students=[student])
        session = AttendanceSession.objects.create(course_info=ci, date=D[0], mode='QR_ONLINE', is_active=False)
        cases = {
            ('MANUAL', 'ABSENT'): 'TEACHER', ('MANUAL', 'PRESENT'): 'TEACHER',
            ('QR_ONLINE', 'PRESENT'): 'QR', ('QR_ONLINE', 'ABSENT'): '', ('QR_OFFLINE', 'PRESENT'): 'QR',
            ('FACE', 'PRESENT'): 'FACE', ('FACE', 'ABSENT'): '', ('HARDWARE', 'PRESENT'): 'FINGERPRINT',
        }
        logs = {
            key: AttendanceLog.objects.create(course_info=ci, student=student, date=D[i], source=key[0],
                                              status=key[1], session=session if key == ('QR_ONLINE', 'ABSENT') else None)
            for i, key in enumerate(cases) if i < 5
        }
        logs.update({
            key: AttendanceLog.objects.create(course_info=ci, student=student, date=D[0], source=key[0],
                                              status=key[1])
            for key in list(cases)[5:]
        })
        kept = AttendanceLog.objects.create(course_info=ci, student=student, date=D[1], source='QR_ONLINE',
                                            status='PRESENT', method='CODE')
        migration.fill_methods(apps, None)
        for key, log in logs.items():
            log.refresh_from_db()
            self.assertEqual(log.method, cases[key], key)
        kept.refresh_from_db()
        self.assertEqual(kept.method, 'CODE')
