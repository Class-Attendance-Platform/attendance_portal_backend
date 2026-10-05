"""
Adding, removing or promoting a student on a day with classes (API v2 section 3): a class held
before someone was added does not count for them; a class held before someone left still does.
"""
import csv
import datetime
import io

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.academic import services
from apps.academic.models import Classroom, StudentClassroom
from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import (
    check_in, client_for, make_admin, make_course_info, make_semester, make_student, make_teacher, start_session,
)

PAST = datetime.date(2026, 9, 1)


class SameDayTestCase(TestCase):
    """a, b from the start; one past class (a present, b absent); `late` is not a member yet."""

    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.tomorrow = self.today + datetime.timedelta(days=1)
        self.teacher = make_teacher('t@example.com')
        self.a = make_student('a@example.com', 2302001)
        self.b = make_student('b@example.com', 2302002)
        self.late = make_student('late@example.com', 2302003)
        self.semester = make_semester(students=[self.a, self.b])
        self.ci = make_course_info(self.teacher, 'CSE301', semester=self.semester)
        self.ci2 = make_course_info(self.teacher, 'CSE303', semester=self.semester)
        AttendanceLog.objects.create(course_info=self.ci, student=self.a, date=PAST, status='PRESENT', method='QR')
        AttendanceLog.objects.create(course_info=self.ci, student=self.b, date=PAST, status='ABSENT')
        self.teacher_client = client_for(self.teacher.user)
        self.admin = client_for(make_admin('admin@example.com'))

    def start(self, ci):
        res = start_session(self.teacher_client, ci)
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['session']['id']

    def stop(self, session_id):
        self.assertEqual(self.teacher_client.post(f'/api/sessions/{session_id}/stop/').status_code, 200)

    def class_today(self, ci, present=()):
        """A live session today in which `present` check in, then stopped (saved)."""
        session_id = self.start(ci)
        for student in present:
            self.assertEqual(check_in(student, session_id).status_code, 200)
        self.stop(session_id)
        return session_id

    def promote(self):
        res = self.admin.post(f'/api/admin/semesters/{self.semester.id}/promote/', {
            'target': {'level': 'Third', 'semester': 'II', 'session': '2025-26'},
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def roster_post(self, path, *students):
        res = self.admin.post(f'/api/admin/semesters/{self.semester.id}/students/{path}', {
            'profile_ids': [str(s.id) for s in students],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def membership(self, student):
        return StudentClassroom.objects.get(student=student, classroom__semester=self.semester)

    def teacher_rows(self, ci):
        res = self.teacher_client.get(f'/api/teacher/course-info/{ci.id}/')
        self.assertEqual(res.status_code, 200)
        return {r['student_id']: r for r in res.data['students']}

    def student_course(self, student, ci):
        res = client_for(student.user).get(f'/api/student/course-info/{ci.id}/')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def csv_rows(self, ci):
        res = self.teacher_client.get(f'/api/reports/course-info/{ci.id}/export/?format=csv')
        self.assertEqual(res.status_code, 200)
        rows = list(csv.reader(io.StringIO(res.content.decode())))
        header = rows[0]
        return {int(row[0]): dict(zip(header, row)) for row in rows[1:]}


class LeavingTests(SameDayTestCase):
    def test_promote_after_todays_class_keeps_it(self):
        self.class_today(self.ci, present=[self.a])
        self.promote()

        self.assertEqual(self.membership(self.a).left_at, self.tomorrow)
        rows = self.teacher_rows(self.ci)
        self.assertEqual({sid: (r['attended'], r['held']) for sid, r in rows.items()},
                         {2302001: (2, 2), 2302002: (0, 2)})  # today still counts for both
        detail = self.student_course(self.a, self.ci)
        self.assertEqual((detail['attended'], detail['held']), (2, 2))
        self.assertEqual((detail['days'][0]['date'], detail['days'][0]['status']), (self.today.isoformat(), 'PRESENT'))
        cells = self.csv_rows(self.ci)
        self.assertEqual(cells[2302001][self.today.isoformat()], 'PRESENT')
        self.assertEqual(cells[2302002][self.today.isoformat()], 'ABSENT')
        self.assertEqual(cells[2302001]['Classes held'], '2')

    def test_check_in_then_promote_then_stop_saves_the_check_in(self):
        session_id = self.start(self.ci)
        self.assertEqual(check_in(self.a, session_id).status_code, 200)
        self.promote()  # a live session today: left_at = tomorrow
        self.assertEqual(self.membership(self.a).left_at, self.tomorrow)
        self.stop(session_id)
        logs = dict(AttendanceLog.objects.filter(session_id=session_id).values_list('student__student_id', 'status'))
        self.assertEqual(logs, {2302001: 'PRESENT', 2302002: 'ABSENT'})

    def test_remove_after_class_keeps_the_day_but_ends_check_ins(self):
        self.class_today(self.ci, present=[self.a])
        res = self.roster_post('remove/', self.a)
        self.assertEqual(res.data['removed'], 1)
        self.assertEqual(self.membership(self.a).left_at, self.tomorrow)

        detail = self.student_course(self.a, self.ci)
        self.assertEqual((detail['attended'], detail['held'], detail['days'][0]['status']), (2, 2, 'PRESENT'))
        summary = client_for(self.a.user).get(f'/api/student/{self.a.id}/semesters/').data['semesters'][0]
        self.assertEqual(summary['left_at'], self.tomorrow.isoformat())
        self.assertEqual(sorted(self.teacher_rows(self.ci)), [2302002])  # no longer on the class list

        # Not current any more: no live sessions, no check-in
        session_id = self.start(self.ci2)
        self.assertEqual(client_for(self.a.user).get('/api/student/live/').data['sessions'], [])
        res = check_in(self.a, session_id)
        self.assertEqual((res.status_code, res.data['code']), (403, 'not_enrolled'))

        # Re-adding the same day makes them current again
        self.assertEqual(self.roster_post('', self.a).data['rejoined'], 1)
        self.assertIsNone(self.membership(self.a).left_at)

    def test_without_a_class_today_they_leave_today(self):
        classroom = Classroom.objects.get(semester=self.semester)
        self.assertEqual(services.leave_date(classroom), self.today)
        self.roster_post('remove/', self.a)
        self.assertEqual(self.membership(self.a).left_at, self.today)


class JoiningTests(SameDayTestCase):
    def test_a_class_held_before_they_were_added_does_not_count(self):
        self.class_today(self.ci, present=[self.a])
        self.assertEqual(self.roster_post('', self.late).data['added'], 1)
        self.assertEqual(self.membership(self.late).joined_at, self.today)

        row = self.teacher_rows(self.ci)[2302003]
        self.assertEqual((row['attended'], row['held'], row['percent'], row['below_min']), (0, 0, None, False))
        detail = self.student_course(self.late, self.ci)
        self.assertEqual((detail['attended'], detail['held'], detail['classes_needed']), (0, 0, 0))
        self.assertEqual([d['status'] for d in detail['days']], [None, None])  # today and the past class
        [semester] = client_for(self.late.user).get(f'/api/student/{self.late.id}/semesters/').data['semesters']
        course = next(c for c in semester['courses'] if c['course']['code'] == 'CSE301')
        self.assertEqual((course['held'], course['totalClasses'], course['history']), (0, 0, []))
        self.assertIsNone(semester['overall_percent'])
        self.assertEqual(self.csv_rows(self.ci)[2302003][self.today.isoformat()], '')
        self.assertEqual(self.csv_rows(self.ci)[2302003]['Classes held'], '0')

    def test_a_later_class_the_same_day_counts(self):
        self.class_today(self.ci, present=[self.a])
        self.roster_post('', self.late)
        session_id = self.start(self.ci2)
        self.assertEqual(check_in(self.late, session_id).status_code, 200)
        self.stop(session_id)

        row = self.teacher_rows(self.ci2)[2302003]
        self.assertEqual((row['attended'], row['held'], row['percent']), (1, 1, 100.0))
        self.assertEqual(self.teacher_rows(self.ci)[2302003]['held'], 0)
        detail = self.student_course(self.late, self.ci2)
        self.assertEqual([(d['date'], d['status']) for d in detail['days']], [(self.today.isoformat(), 'PRESENT')])

    def test_a_roll_call_after_they_joined_counts_for_them(self):
        self.class_today(self.ci, present=[self.a])
        self.roster_post('', self.late)
        res = self.teacher_client.post(f'/api/teacher/course-info/{self.ci.id}/roll-call/', {
            'date': self.today.isoformat(), 'present_profile_ids': [str(self.a.id)],
        }, format='json')
        self.assertEqual(res.status_code, 200)
        row = self.teacher_rows(self.ci)[2302003]
        self.assertEqual((row['attended'], row['held']), (0, 1))  # the roll call listed them: absent
