"""backfill_join_dates: join dates for students added mid-semester under the old app (API v2 section 9)."""
import datetime
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.academic.models import StudentClassroom
from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import client_for, make_course_info, make_semester, make_student, make_teacher

D1, D2, D3, D4 = (datetime.date(2026, 9, d) for d in (1, 2, 3, 4))


def run(*args):
    out = StringIO()
    call_command('backfill_join_dates', *args, stdout=out)
    return out.getvalue()


class BackfillJoinDatesTests(TestCase):
    """
    Old data (every membership joined_at null). CSE301 classes on D1, D2, D3; CSE303 on D2.
    - early: logged on every date (there from the start)
    - late: logged from D2 on (added after D1's class), present on D2 and D3
    - never: no logs at all (added after the last class)
    - gone: logged on D1 only, left on D2 (D3 is after they left)
    """

    def setUp(self):
        self.teacher = make_teacher('t@example.com')
        self.early = make_student('early@example.com', 2302001)
        self.late = make_student('late@example.com', 2302002, first_name='Lata', last_name='Das')
        self.never = make_student('never@example.com', 2302003)
        self.gone = make_student('gone@example.com', 2302004)
        self.semester = make_semester(students=[self.early, self.late, self.never, self.gone])
        StudentClassroom.objects.filter(student=self.gone).update(left_at=D2)
        self.ci = make_course_info(self.teacher, 'CSE301', semester=self.semester)
        self.ci2 = make_course_info(self.teacher, 'CSE303', semester=self.semester)
        for ci, student, day, status in [
            (self.ci, self.early, D1, 'PRESENT'), (self.ci, self.early, D2, 'ABSENT'),
            (self.ci, self.early, D3, 'PRESENT'), (self.ci2, self.early, D2, 'PRESENT'),
            (self.ci, self.gone, D1, 'PRESENT'),
            (self.ci, self.late, D2, 'PRESENT'), (self.ci, self.late, D3, 'PRESENT'),
            (self.ci2, self.late, D2, 'ABSENT'),
        ]:
            AttendanceLog.objects.create(course_info=ci, student=student, date=day, status=status)

    def joined(self, student):
        return StudentClassroom.objects.get(student=student).joined_at

    def test_dry_run_lists_the_changes_and_writes_nothing(self):
        out = run()
        self.assertIn("Students who joined after their semester's first class: 2", out)
        self.assertIn(
            '2302002 Lata Das · Level 3 · Term I · 2025-26 (Main): joined_at 2026-09-02; '
            'classes held 4 -> 3, attended 2, 50.0% -> 66.7%', out,
        )
        self.assertIn('2302003 Student 2302003 · Level 3 · Term I · 2025-26 (Main): joined_at 2026-09-03; '
                      'classes held 4 -> 0, attended 0, 0.0% -> -', out)
        self.assertNotIn('2302001', out)
        self.assertNotIn('2302004', out)
        self.assertIn('Dry run', out)
        self.assertFalse(StudentClassroom.objects.filter(joined_at__isnull=False).exists())

    def test_apply_writes_the_guessed_dates(self):
        before = client_for(self.late.user).get(f'/api/student/{self.late.id}/semesters/').data['semesters'][0]
        self.assertEqual(before['overall_percent'], 50.0)  # D1 counted as absent

        self.assertIn('Join dates written: 2.', run('--apply'))
        self.assertEqual(self.joined(self.late), D2)
        self.assertEqual(self.joined(self.never), D3)   # the last class date: it does not count without a log
        self.assertIsNone(self.joined(self.early))       # there from the start
        self.assertIsNone(self.joined(self.gone))        # logged on the only class date before leaving

        [semester] = client_for(self.late.user).get(f'/api/student/{self.late.id}/semesters/').data['semesters']
        numbers = {c['course']['code']: (c['attended'], c['held']) for c in semester['courses']}
        self.assertEqual(numbers, {'CSE301': (2, 2), 'CSE303': (0, 1)})
        [semester] = client_for(self.never.user).get(f'/api/student/{self.never.id}/semesters/').data['semesters']
        self.assertEqual([c['held'] for c in semester['courses']], [0, 0])
        self.assertIsNone(semester['overall_percent'])

        # Running it again finds nothing new
        self.assertIn('nothing to do', run('--apply'))

    def test_a_join_date_already_set_is_kept(self):
        StudentClassroom.objects.filter(student=self.late).update(joined_at=D3)
        run('--apply')
        self.assertEqual(self.joined(self.late), D3)
