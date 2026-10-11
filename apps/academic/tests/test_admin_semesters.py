"""Admin: semesters, roster, courses taught, reassign, promote, courses (API v2 section 3)."""
import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import (
    client_for, make_admin, make_course_info, make_semester, make_student, make_teacher,
)
from apps.users.models import StudentProfile

SEMESTER_ROW_KEYS = {
    'id', 'level', 'semester', 'session', 'label', 'start_date', 'end_date', 'is_active', 'deleted',
    'classroom_id', 'student_count', 'course_count',
}
DAY = datetime.date(2026, 9, 1)


def membership(student, semester):
    return StudentClassroom.objects.get(student=student, classroom__semester=semester)


class AdminTestCase(TestCase):
    def setUp(self):
        self.admin = client_for(make_admin('admin@example.com'))
        self.today = timezone.localdate()


class SemesterListTests(AdminTestCase):
    def test_row_and_counts(self):
        a = make_student('a@example.com', 2302001)
        b = make_student('b@example.com', 2302002)
        gone = make_student('gone@example.com', 2302003, deleted=True, is_active=False)
        left = make_student('left@example.com', 2302004)
        semester = make_semester(students=[a, b, gone, left], start_date=DAY)
        StudentClassroom.objects.filter(student=left).update(left_at=self.today)
        teacher = make_teacher('t@example.com')
        make_course_info(teacher, 'CSE301', semester=semester)
        deleted_course = make_course_info(teacher, 'CSE303', semester=semester)
        Course.objects.filter(pk=deleted_course.course_id).update(deleted=True)
        removed = make_course_info(teacher, 'CSE305', semester=semester)
        CourseInfo.objects.filter(pk=removed.pk).update(deleted=True)

        res = self.admin.get('/api/admin/semesters/')

        self.assertEqual(res.status_code, 200)
        row = res.data['semesters'][0]
        self.assertEqual(set(row), SEMESTER_ROW_KEYS)
        self.assertEqual(row['label'], 'Level 3 · Term I · 2025-26')
        self.assertEqual(row['session'], '2025-26')
        self.assertEqual(row['start_date'], '2026-09-01')
        self.assertIsNone(row['end_date'])
        self.assertEqual(row['classroom_id'], str(Classroom.objects.get(semester=semester).id))
        self.assertEqual(row['student_count'], 2)  # not the deleted account, not the former member
        self.assertEqual(row['course_count'], 1)   # not the deleted course, not the removed one

    def test_finished_semester_counts_everyone_who_was_in_it(self):
        a = make_student('a@example.com', 2302001)
        b = make_student('b@example.com', 2302002)
        semester = make_semester(students=[a, b], is_active=False)
        StudentClassroom.objects.filter(student=a).update(left_at=self.today)  # promoted
        row = self.admin.get(f'/api/admin/semesters/{semester.id}/').data['semester']
        self.assertEqual(row['student_count'], 2)

    def test_order_and_status_filter(self):
        old = make_semester('Third', 'I', '2024-25', is_active=False)
        l1 = make_semester('First', 'II', '2025-26')
        l4 = make_semester('Fourth', 'I', '2025-26')
        l2_new = make_semester('Second', 'I', '2026-27')
        no_session = make_semester('Third', 'II', '')
        gone = make_semester('Second', 'II', '2025-26', deleted=True, is_active=False)

        def ids(query=''):
            res = self.admin.get(f'/api/admin/semesters/{query}')
            self.assertEqual(res.status_code, 200)
            return [r['id'] for r in res.data['semesters']]

        order = [str(s.id) for s in (l2_new, l1, l4, no_session, old)]
        self.assertEqual(ids(), order)
        self.assertEqual(ids('?status=all'), order)
        self.assertEqual(ids('?status=active'), order[:4])
        self.assertEqual(ids('?status=finished'), [str(old.id)])
        self.assertEqual(ids('?status=deleted'), [str(gone.id)])
        res = self.admin.get('/api/admin/semesters/?status=old')
        self.assertEqual((res.status_code, res.data['code']), (400, 'invalid_status'))

    def test_admins_only(self):
        teacher = make_teacher('t@example.com')
        self.assertEqual(client_for(teacher.user).get('/api/admin/semesters/').status_code, 403)


class SemesterCreateTests(AdminTestCase):
    def post(self, **body):
        data = {'level': 'Third', 'semester': 'I', 'session': '2025-26', **body}
        return self.admin.post('/api/admin/semesters/', data, format='json')

    def test_creates_the_semester_and_its_main_classroom(self):
        res = self.post(start_date='2026-01-10', end_date='2026-06-30')
        self.assertEqual(res.status_code, 201)
        row = res.data['semester']
        semester = Semester.objects.get(id=row['id'])
        classroom = Classroom.objects.get(semester=semester)
        self.assertEqual(classroom.name, 'Main')
        self.assertEqual(row['classroom_id'], str(classroom.id))
        self.assertEqual((row['is_active'], row['deleted'], row['student_count'], row['course_count']),
                         (True, False, 0, 0))
        self.assertEqual((row['start_date'], row['end_date']), ('2026-01-10', '2026-06-30'))

    def test_session_is_tidied(self):
        for text in ('2025-2026', '2025/26', ' 2025 - 26 '):
            Semester.objects.all().delete()
            res = self.post(session=text)
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(res.data['semester']['session'], '2025-26')

    def test_bad_values(self):
        cases = [
            ({'session': '2025'}, 'session', 'Session must look like 2025-26.'),
            ({'session': '2025-27'}, 'session', 'Session must look like 2025-26.'),
            ({'session': ''}, 'session', 'Session is required.'),
            ({'level': 'Fifth'}, 'level', 'Level must be First, Second, Third or Fourth.'),
            ({'semester': 'III'}, 'semester', 'Term must be I or II.'),
            ({'semester': None}, 'semester', 'Term is required.'),
            ({'start_date': '2026-06-30', 'end_date': '2026-01-10'}, 'end_date',
             'End date must be on or after the start date.'),
        ]
        for body, field, message in cases:
            res = self.post(**body)
            self.assertEqual(res.status_code, 400, body)
            self.assertIn(field, res.data['errors'])
            self.assertEqual(res.data['message'], message)
        self.assertFalse(Semester.objects.exists())

    def test_one_active_semester_per_level(self):
        make_semester('Third', 'I', '2025-26')
        res = self.post(semester='II')
        self.assertEqual((res.status_code, res.data['code']), (400, 'level_has_active_semester'))
        self.assertEqual(res.data['message'], 'Level 3 already has an active semester. Finish it first.')
        # Finished or deleted semesters of the level do not block; other levels never do
        Semester.objects.update(is_active=False)
        self.assertEqual(self.post(semester='II').status_code, 201)
        self.assertEqual(self.post(level='Fourth').status_code, 201)


class SemesterEditTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.semester = make_semester('Third', 'I', '')
        self.url = f'/api/admin/semesters/{self.semester.id}/'

    def test_patch_session_and_dates_only(self):
        res = self.admin.patch(self.url, {'session': '2025-2026', 'start_date': '2026-01-10',
                                          'level': 'First', 'semester': 'II'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.semester.refresh_from_db()
        self.assertEqual((self.semester.session, self.semester.start_date, self.semester.level, self.semester.semester),
                         ('2025-26', datetime.date(2026, 1, 10), 'Third', 'I'))
        self.assertEqual(res.data['semester']['label'], 'Level 3 · Term I · 2025-26')

        res = self.admin.patch(self.url, {'end_date': '2026-01-01'}, format='json')  # before the saved start
        self.assertEqual(res.status_code, 400)
        self.assertIn('end_date', res.data['errors'])
        res = self.admin.put(self.url, {'end_date': '2026-06-30'}, format='json')  # the old app sends PUT
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['semester']['end_date'], '2026-06-30')

    def test_delete_restore_finish_reopen(self):
        self.assertEqual(self.admin.delete(self.url).status_code, 200)
        self.semester.refresh_from_db()
        self.assertEqual((self.semester.deleted, self.semester.is_active), (True, False))
        self.assertEqual(self.admin.get(self.url).data['semester']['deleted'], True)  # still readable
        self.assertEqual(self.admin.delete(self.url).status_code, 404)
        self.assertEqual(self.admin.patch(self.url, {'session': '2025-26'}, format='json').status_code, 404)
        self.assertEqual(self.admin.post(f'{self.url}finish/').status_code, 404)

        res = self.admin.post(f'{self.url}restore/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data['semester']['deleted'], res.data['semester']['is_active']), (False, False))

        res = self.admin.post(f'{self.url}reopen/')
        self.assertEqual((res.status_code, res.data['semester']['is_active']), (200, True))
        res = self.admin.post(f'{self.url}finish/')
        self.assertEqual((res.status_code, res.data['semester']['is_active']), (200, False))

    def test_reopen_checks_the_level(self):
        Semester.objects.filter(pk=self.semester.pk).update(is_active=False)
        make_semester('Third', 'II', '2025-26')
        res = self.admin.post(f'{self.url}reopen/')
        self.assertEqual((res.status_code, res.data['code']), (400, 'level_has_active_semester'))
        self.semester.refresh_from_db()
        self.assertFalse(self.semester.is_active)


class RosterTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.a = make_student('a@example.com', 2302001, first_name='Ayesha', last_name='Rahman')
        self.b = make_student('b@example.com', 2302002)
        self.c = make_student('c@example.com', 2302003)
        self.semester = make_semester()
        self.url = f'/api/admin/semesters/{self.semester.id}/students/'

    def add(self, *students):
        return self.admin.post(self.url, {'profile_ids': [str(s.id) for s in students]}, format='json')

    def remove(self, *students):
        return self.admin.post(f'{self.url}remove/', {'profile_ids': [str(s.id) for s in students]}, format='json')

    def roster(self, query=''):
        res = self.admin.get(f'{self.url}{query}')
        self.assertEqual(res.status_code, 200)
        return res.data['students']

    def test_add_before_the_first_class_counts_from_the_start(self):
        res = self.add(self.a, self.b, self.a)
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data['added'], res.data['rejoined'], res.data['already_in']), (2, 0, 0))
        self.assertEqual(res.data['message'], '2 students added.')
        self.assertEqual(self.roster(), [
            {'profile_id': str(self.a.id), 'student_id': 2302001, 'name': 'Ayesha Rahman', 'email': 'a@example.com',
             'joined_at': None, 'left_at': None},
            {'profile_id': str(self.b.id), 'student_id': 2302002, 'name': 'Student 2302002', 'email': 'b@example.com',
             'joined_at': None, 'left_at': None},
        ])
        self.assertEqual(self.add(self.a).data['already_in'], 1)

    def test_late_joiner_counts_from_today(self):
        ci = make_course_info(make_teacher('t@example.com'), semester=self.semester)
        AttendanceLog.objects.create(course_info=ci, student=self.a, date=DAY, status='PRESENT')
        self.add(self.b)
        self.assertEqual(membership(self.b, self.semester).joined_at, self.today)

    def test_remove_keeps_history_and_readding_keeps_joined_at(self):
        self.add(self.a, self.b)
        StudentClassroom.objects.filter(student=self.a).update(joined_at=DAY)

        res = self.remove(self.a, self.c)  # c was never a member
        self.assertEqual((res.status_code, res.data['removed']), (200, 1))
        self.assertEqual(res.data['message'], '1 student removed.')
        self.assertEqual(membership(self.a, self.semester).left_at, self.today)
        self.assertEqual([r['student_id'] for r in self.roster()], [2302002])
        rows = self.roster('?include_left=true')
        self.assertEqual([(r['student_id'], r['left_at']) for r in rows],
                         [(2302002, None), (2302001, self.today.isoformat())])  # current first

        res = self.add(self.a)
        self.assertEqual(res.data['rejoined'], 1)
        m = membership(self.a, self.semester)
        self.assertEqual((m.joined_at, m.left_at), (DAY, None))

    def test_unknown_or_deleted_students_are_rejected(self):
        gone = make_student('gone@example.com', 2302009, deleted=True, is_active=False)
        res = self.add(self.a, gone)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Some of these students were not found.')
        self.assertFalse(StudentClassroom.objects.exists())
        res = self.admin.post(self.url, {'profile_ids': []}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Students: choose at least one.')

    def test_deleted_accounts_are_not_listed(self):
        self.add(self.a, self.b)
        self.b.user.deleted = True
        self.b.user.save()
        self.assertEqual([r['student_id'] for r in self.roster('?include_left=true')], [2302001])

    def test_deleted_semester(self):
        self.semester.deleted = True
        self.semester.save()
        self.assertEqual(self.add(self.a).status_code, 404)
        self.assertEqual(self.remove(self.a).status_code, 404)
        self.assertEqual(self.roster(), [])

    def test_semester_without_a_class_group_gets_one(self):
        Classroom.objects.filter(semester=self.semester).delete()
        self.assertEqual(self.roster(), [])
        self.assertEqual(self.add(self.a).status_code, 200)
        self.assertEqual(Classroom.objects.get(semester=self.semester).name, 'Main')


class SemesterCoursesTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.semester = make_semester()
        self.teacher = make_teacher('t@example.com', first_name='Nadia', last_name='Islam')
        self.other = make_teacher('o@example.com')
        self.course = Course.objects.create(code='CSE301', title='Software Engineering', credits='CREDIT_3_00')
        self.url = f'/api/admin/semesters/{self.semester.id}/courses/'

    def add(self, course=None, teacher='default'):
        body = {'course_id': str((course or self.course).id)}
        if teacher != 'default':
            body['teacher_id'] = str(teacher.id) if teacher else None
        else:
            body['teacher_id'] = str(self.teacher.id)
        return self.admin.post(self.url, body, format='json')

    def test_add_and_list(self):
        res = self.add()
        self.assertEqual(res.status_code, 201)
        ci = CourseInfo.objects.get()
        self.assertEqual(ci.classroom, Classroom.objects.get(semester=self.semester))
        expected = {
            'course_info_id': str(ci.id),
            'course': {'id': str(self.course.id), 'code': 'CSE301', 'title': 'Software Engineering',
                       'credits': 'CREDIT_3_00'},
            'teacher': {'id': str(self.teacher.id), 'name': 'Nadia Islam', 'deleted': False},
        }
        self.assertEqual(res.data['course'], expected)
        self.assertEqual(self.admin.get(self.url).data['courses'], [expected])

    def test_a_deleted_teacher_is_marked(self):
        self.add()
        self.teacher.user.deleted = True
        self.teacher.user.save(update_fields=['deleted'])
        row = self.admin.get(self.url).data['courses'][0]
        self.assertEqual((row['teacher']['name'], row['teacher']['deleted']), ('Nadia Islam', True))

    def test_without_a_teacher(self):
        res = self.add(teacher=None)
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data['course']['teacher'])

    def test_bad_requests(self):
        self.add()
        res = self.add()
        self.assertEqual((res.status_code, res.data['code']), (400, 'course_already_added'))
        self.assertEqual(res.data['message'], 'CSE301 is already taught in this semester.')

        gone = Course.objects.create(code='CSE303', title='Gone', deleted=True)
        res = self.add(course=gone)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'This course was not found.')

        other = Course.objects.create(code='CSE305', title='Other')
        self.other.user.deleted = True
        self.other.user.save()
        res = self.add(course=other, teacher=self.other)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'This teacher was not found.')
        self.assertEqual(CourseInfo.objects.count(), 1)

    def test_adding_a_removed_course_again_brings_back_its_history(self):
        ci_id = self.add().data['course']['course_info_id']
        student = make_student('a@example.com', 2302001)
        AttendanceLog.objects.create(course_info_id=ci_id, student=student, date=DAY, status='PRESENT')
        self.assertEqual(self.admin.delete(f'/api/admin/course-info/{ci_id}/').status_code, 200)
        self.assertEqual(self.admin.get(self.url).data['courses'], [])

        res = self.add(teacher=self.other)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['course']['course_info_id'], ci_id)
        self.assertEqual(res.data['course']['teacher']['id'], str(self.other.id))
        self.assertEqual(AttendanceLog.objects.filter(course_info_id=ci_id).count(), 1)

    def test_reassign_and_delete(self):
        ci_id = self.add().data['course']['course_info_id']
        url = f'/api/admin/course-info/{ci_id}/'
        detail = f'/api/teacher/course-info/{ci_id}/'
        self.assertEqual(client_for(self.teacher.user).get(detail).status_code, 200)

        res = self.admin.patch(url, {'teacher_id': str(self.other.id)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['course_info']['teacher']['id'], str(self.other.id))
        self.assertEqual(client_for(self.other.user).get(detail).status_code, 200)   # sees the history
        self.assertEqual(client_for(self.teacher.user).get(detail).status_code, 403)

        res = self.admin.patch(url, {'teacher_id': None}, format='json')
        self.assertEqual((res.status_code, res.data['course_info']['teacher']), (200, None))
        res = self.admin.patch(url, {'teacher_id': '6f1c1f2e-1111-4a4a-9b9b-123456789abc'}, format='json')
        self.assertEqual((res.status_code, res.data['message']), (400, 'This teacher was not found.'))
        res = self.admin.patch(url, {}, format='json')
        self.assertEqual((res.status_code, res.data['message']), (400, 'Teacher is required.'))

        self.assertEqual(self.admin.delete(url).status_code, 200)
        self.assertTrue(CourseInfo.objects.get(id=ci_id).deleted)
        self.assertEqual(self.admin.delete(url).status_code, 404)
        self.assertEqual(self.admin.patch(url, {'teacher_id': None}, format='json').status_code, 404)


class PromoteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.a = make_student('a@example.com', 2302001)
        self.b = make_student('b@example.com', 2302002)
        self.c = make_student('c@example.com', 2302003)
        self.gone = make_student('gone@example.com', 2302004, deleted=True, is_active=False)
        self.source = make_semester('Third', 'I', '2025-26', students=[self.a, self.b, self.c, self.gone])
        StudentClassroom.objects.filter(student=self.c).update(left_at=DAY)  # already left
        self.url = f'/api/admin/semesters/{self.source.id}/promote/'

    def promote(self, **body):
        return self.admin.post(self.url, body, format='json')

    def test_to_a_new_semester_of_the_same_level(self):
        res = self.promote(target={'level': 'Third', 'semester': 'II', 'session': '2025-26'})

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['moved'], 2)  # not c (already left), not the deleted account
        self.assertEqual(res.data['message'], '2 students moved to Level 3 · Term II · 2025-26.')
        target = Semester.objects.get(id=res.data['target_semester_id'])
        self.assertEqual((target.level, target.semester, target.is_active), ('Third', 'II', True))
        self.source.refresh_from_db()
        self.assertEqual((self.source.is_active, self.source.deleted), (False, False))  # finished, never deleted

        for student in (self.a, self.b):
            old, new = membership(student, self.source), membership(student, target)
            self.assertEqual(old.left_at, self.today)
            self.assertEqual((new.joined_at, new.left_at), (None, None))  # before its first class
            student.refresh_from_db()
            self.assertEqual((student.current_level, student.current_semester), ('Third', 'II'))
        self.assertEqual(membership(self.c, self.source).left_at, DAY)
        self.assertFalse(StudentClassroom.objects.filter(student=self.c, classroom__semester=target).exists())

        # History stays on the finished source
        row = self.admin.get(f'/api/admin/semesters/{self.source.id}/').data['semester']
        self.assertEqual(row['student_count'], 3)
        self.assertEqual(len(self.admin.get(f'/api/admin/semesters/{self.source.id}/students/'
                                            '?include_left=true').data['students']), 3)
        self.assertEqual(self.admin.get(f'/api/admin/semesters/{target.id}/').data['semester']['student_count'], 2)

    def test_some_students_into_an_existing_semester(self):
        target = make_semester('Fourth', 'I', '2025-26')
        ci = make_course_info(make_teacher('t@example.com'), semester=target)
        AttendanceLog.objects.create(course_info=ci, student=self.b, date=DAY, status='PRESENT')  # it has classes
        res = self.promote(target_semester_id=str(target.id), profile_ids=[str(self.a.id)])
        self.assertEqual((res.status_code, res.data['moved']), (200, 1))
        self.assertEqual(membership(self.a, target).joined_at, self.today)  # a late joiner there
        self.assertIsNone(membership(self.b, self.source).left_at)
        self.a.refresh_from_db()
        self.assertEqual(self.a.current_level, 'Fourth')
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)

    def test_a_matching_semester_is_reused(self):
        target = make_semester('Fourth', 'I', '2025-26')
        res = self.promote(target={'level': 'Fourth', 'semester': 'I', 'session': '2025-2026'})
        self.assertEqual((res.status_code, res.data['target_semester_id']), (200, str(target.id)))
        self.assertEqual(Semester.objects.count(), 2)

    def test_bad_requests(self):
        other_level = make_semester('Fourth', 'I', '2024-25')
        cases = [
            ({}, 400, None),
            ({'target': {'level': 'Third', 'semester': 'II', 'session': '2025-26'},
              'target_semester_id': str(other_level.id)}, 400, None),
            ({'target': {'level': 'Fourth', 'semester': 'II', 'session': '2025-26'}}, 400,
             'level_has_active_semester'),
            ({'target_semester_id': str(self.source.id)}, 400, None),
            ({'target': {'level': 'Third', 'semester': 'I', 'session': '2025-26'}}, 400, None),  # the source
            ({'target_semester_id': '6f1c1f2e-1111-4a4a-9b9b-123456789abc'}, 400, None),
            ({'target_semester_id': str(other_level.id), 'profile_ids': [str(self.c.id)]}, 400, None),
            ({'target': {'level': 'Third', 'semester': 'II', 'session': 'next'}}, 400, None),
        ]
        for body, code, error_code in cases:
            res = self.promote(**body)
            self.assertEqual(res.status_code, code, body)
            self.assertTrue(res.data['message'])
            if error_code:
                self.assertEqual(res.data['code'], error_code)
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_active)  # nothing happened
        self.assertEqual(Semester.objects.count(), 2)
        self.assertFalse(StudentClassroom.objects.filter(left_at=self.today).exists())


class CourseTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(code='CSE301', title='Software Engineering')
        self.url = f'/api/admin/courses/{self.course.id}/'

    def test_patch_is_partial(self):
        res = self.admin.patch(self.url, {'title': 'Software Engineering I'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data['course']['code'], res.data['course']['title']), ('CSE301', 'Software Engineering I'))
        self.assertFalse(res.data['course']['deleted'])

    def test_duplicate_code(self):
        res = self.admin.post('/api/admin/courses/', {'code': 'cse301', 'title': 'Again'}, format='json')
        self.assertEqual((res.status_code, res.data['message']), (400, 'A course with the code CSE301 already exists.'))
        self.course.deleted = True
        self.course.save()
        res = self.admin.post('/api/admin/courses/', {'code': 'CSE301', 'title': 'Again'}, format='json')
        self.assertEqual(res.data['message'],
                         'A course with the code CSE301 already exists. It is deleted: restore it instead.')

    def test_delete_restore_and_status(self):
        teacher = make_teacher('t@example.com')
        semester = make_semester()
        ci = CourseInfo.objects.create(course=self.course, teacher=teacher, semester=semester,
                                       classroom=Classroom.objects.get(semester=semester))
        teacher_courses = f'/api/teacher/{teacher.id}/courses/'
        self.assertEqual(len(client_for(teacher.user).get(teacher_courses).data['current']), 1)

        self.assertEqual(self.admin.delete(self.url).status_code, 200)
        self.assertEqual(self.admin.delete(self.url).status_code, 404)
        self.assertEqual(self.admin.get('/api/admin/courses/').data['courses'], [])
        self.assertEqual([c['code'] for c in self.admin.get('/api/admin/courses/?status=deleted').data['courses']],
                         ['CSE301'])
        self.assertEqual(self.admin.get('/api/admin/courses/?status=gone').data['code'], 'invalid_status')
        # Hidden from teachers too
        self.assertEqual(client_for(teacher.user).get(teacher_courses).data['current'], [])
        self.assertEqual(client_for(teacher.user).get(f'/api/teacher/course-info/{ci.id}/').status_code, 404)
        self.assertEqual(self.admin.get(f'/api/admin/semesters/{semester.id}/courses/').data['courses'], [])

        res = self.admin.post(f'{self.url}restore/')
        self.assertEqual((res.status_code, res.data['course']['deleted']), (200, False))
        self.assertEqual(len(client_for(teacher.user).get(teacher_courses).data['current']), 1)
        self.assertEqual(self.admin.get(self.url).data['course']['code'], 'CSE301')


class MainClassroomMigrationTests(TestCase):
    def test_semesters_without_a_class_group_get_one(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module('apps.academic.migrations.0003_main_classroom_for_every_semester')
        bare = Semester.objects.create(level='First', semester='I')
        only_deleted = Semester.objects.create(level='Second', semester='I')
        Classroom.objects.create(name='Main', semester=only_deleted, deleted=True)
        has_one = make_semester('Third', 'I')
        migration.add_missing_main_classrooms(apps, None)
        self.assertEqual(Classroom.objects.get(semester=bare).name, 'Main')
        self.assertEqual(Classroom.objects.filter(semester=only_deleted, deleted=False).count(), 1)
        self.assertEqual(Classroom.objects.filter(semester=has_one).count(), 1)


class OneCurrentSemesterTests(AdminTestCase):
    """A student may be a current member of one active semester only (409 already_in_semester)."""

    def setUp(self):
        super().setUp()
        self.a = make_student('a@example.com', 2302001, first_name='Ayesha', last_name='Rahman')
        self.b = make_student('b@example.com', 2302002, first_name='Tanvir', last_name='Hasan')
        self.c = make_student('c@example.com', 2302003)
        self.current = make_semester('Third', 'I', '2025-26', students=[self.a, self.b])
        self.other = make_semester('Second', 'I', '2025-26')

    def add(self, semester, *students):
        return self.admin.post(f'/api/admin/semesters/{semester.id}/students/',
                               {'profile_ids': [str(s.id) for s in students]}, format='json')

    def members(self, semester):
        return set(StudentClassroom.objects.filter(classroom__semester=semester).values_list('student_id', flat=True))

    def test_adding_a_current_member_of_another_active_semester_is_refused(self):
        res = self.add(self.other, self.a, self.c)
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_in_semester'))
        self.assertEqual(res.data['message'], 'Ayesha Rahman (2302001) is already in Level 3 · Term I · 2025-26. '
                                              'Remove them from that semester first.')
        self.assertEqual(res.data['students'], [{
            'profile_id': str(self.a.id), 'student_id': 2302001, 'name': 'Ayesha Rahman',
            'semester_id': str(self.current.id), 'semester_label': 'Level 3 · Term I · 2025-26',
        }])
        self.assertEqual(self.members(self.other), set())  # nothing added, not even c

        res = self.add(self.other, self.c, self.b, self.a)
        self.assertEqual(res.data['message'], '2 students are already in another semester: Ayesha Rahman (2302001), '
                                              'Tanvir Hasan (2302002). Remove them there first.')
        self.assertEqual([s['student_id'] for s in res.data['students']], [2302001, 2302002])

    def test_a_long_list_is_shortened_in_the_message(self):
        many = [make_student(f's{i}@example.com', 2302100 + i) for i in range(7)]
        StudentClassroom.objects.bulk_create([
            StudentClassroom(student=s, classroom=Classroom.objects.get(semester=self.current)) for s in many
        ])
        res = self.add(self.other, *many)
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.data['message'].startswith('7 students are already in another semester: '
                                                       'Student 2302100 (2302100), '), res.data['message'])
        self.assertTrue(res.data['message'].endswith(' and 2 more. Remove them there first.'), res.data['message'])
        self.assertEqual(len(res.data['students']), 7)

    def test_former_members_finished_semesters_and_the_same_semester_do_not_block(self):
        self.assertEqual(self.admin.post(f'/api/admin/semesters/{self.current.id}/students/remove/',
                                         {'profile_ids': [str(self.a.id)]}, format='json').status_code, 200)
        self.assertEqual(self.add(self.other, self.a).status_code, 200)    # left the other one
        self.assertEqual(self.add(self.current, self.b).data['already_in'], 1)  # already here: no clash
        Semester.objects.filter(pk=self.current.pk).update(is_active=False)
        self.assertEqual(self.add(self.other, self.b).status_code, 200)    # finished: not current any more

    def test_promote_still_moves_them(self):
        res = self.admin.post(f'/api/admin/semesters/{self.current.id}/promote/',
                              {'target': {'level': 'Third', 'semester': 'II', 'session': '2025-26'}}, format='json')
        self.assertEqual((res.status_code, res.data['moved']), (200, 2))
        current = StudentClassroom.objects.current().filter(student=self.a, classroom__semester__is_active=True)
        self.assertEqual([str(m.classroom.semester_id) for m in current], [res.data['target_semester_id']])

    def test_promote_is_refused_for_a_student_in_a_third_active_semester(self):
        # An older state: a is also a current member of `other`
        StudentClassroom.objects.create(student=self.a, classroom=Classroom.objects.get(semester=self.other))
        res = self.admin.post(f'/api/admin/semesters/{self.current.id}/promote/',
                              {'target': {'level': 'Third', 'semester': 'II', 'session': '2025-26'}}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_in_semester'))
        self.assertEqual(res.data['students'][0]['semester_id'], str(self.other.id))
        self.current.refresh_from_db()
        self.assertTrue(self.current.is_active)  # nothing changed: no new semester, nobody moved
        self.assertFalse(Semester.objects.filter(level='Third', semester='II').exists())
        self.assertFalse(StudentClassroom.objects.filter(left_at__isnull=False).exists())

    def test_reopening_is_refused_while_its_members_are_in_another_active_semester(self):
        url = f'/api/admin/semesters/{self.current.id}/'
        self.assertEqual(self.admin.post(f'{url}finish/').status_code, 200)  # members stay (no promote)
        self.assertEqual(self.add(self.other, self.a).status_code, 200)

        res = self.admin.post(f'{url}reopen/')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_in_semester'))
        self.assertEqual(res.data['message'], 'Ayesha Rahman (2302001) is now in Level 2 · Term I · 2025-26. '
                                              'Remove them from this semester before reopening it.')
        self.current.refresh_from_db()
        self.assertFalse(self.current.is_active)

        self.admin.post(f'{url}students/remove/', {'profile_ids': [str(self.a.id)]}, format='json')
        res = self.admin.post(f'{url}reopen/')
        self.assertEqual((res.status_code, res.data['semester']['is_active']), (200, True))

    def test_reopening_counts_deleted_accounts_too(self):
        # Restoring the account later would otherwise make two current semesters
        Semester.objects.filter(pk=self.current.pk).update(is_active=False)
        StudentClassroom.objects.create(student=self.b, classroom=Classroom.objects.get(semester=self.other))
        self.b.user.deleted = True
        self.b.user.save()
        url = f'/api/admin/semesters/{self.current.id}/'
        res = self.admin.post(f'{url}reopen/')
        self.assertEqual((res.status_code, [s['student_id'] for s in res.data['students']]), (409, [2302002]))
        # The roster hides deleted accounts, so the message says how to clear them
        self.assertEqual(res.data['message'], 'Tanvir Hasan (2302002) is now in Level 2 · Term I · 2025-26. '
                                              'Remove them from this semester before reopening it (account deleted: '
                                              'restore it on the Students page, remove them, then reopen).')
        StudentClassroom.objects.create(student=self.a, classroom=Classroom.objects.get(semester=self.other))
        res = self.admin.post(f'{url}reopen/')
        self.assertEqual(res.data['message'], '2 students are now in another semester: Ayesha Rahman (2302001), '
                                              'Tanvir Hasan (2302002, account deleted). Remove them from this '
                                              'semester before reopening it (account deleted: restore it on the '
                                              'Students page, remove them, then reopen).')
        self.a.user.deleted = True
        self.a.user.save()
        res = self.admin.post(f'{url}reopen/')
        self.assertEqual(res.data['message'], '2 students are now in another semester: Ayesha Rahman (2302001, '
                                              'account deleted), Tanvir Hasan (2302002, account deleted). Remove '
                                              'them from this semester before reopening it (deleted accounts: '
                                              'restore them on the Students page, remove them, then reopen).')

        # The way out the message gives
        for student in (self.a, self.b):
            self.assertEqual(self.admin.post(f'/api/admin/students/{student.id}/restore/').status_code, 200)
        res = self.admin.post(f'{url}students/remove/', {'profile_ids': [str(self.a.id), str(self.b.id)]},
                              format='json')
        self.assertEqual(res.data['removed'], 2)
        self.assertEqual(self.admin.post(f'{url}reopen/').status_code, 200)

    def test_the_older_classroom_endpoint_follows_the_rule(self):
        classroom = Classroom.objects.get(semester=self.other)
        res = self.admin.post(f'/api/admin/classrooms/{classroom.id}/students/',
                              {'student_ids': [str(self.a.id), str(self.c.id)]}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_in_semester'))
        self.assertEqual(self.members(self.other), set())
        res = self.admin.post(f'/api/admin/classrooms/{classroom.id}/students/',
                              {'student_ids': [str(self.c.id)]}, format='json')
        self.assertEqual((res.status_code, res.data['added']), (200, [str(self.c.id)]))

    def test_moving_an_older_class_group_to_another_semester_follows_the_rule(self):
        old = make_semester('First', 'I', '2024-25', students=[self.c], is_active=False)  # c is still a member
        self.assertEqual(self.add(self.other, self.c).status_code, 200)  # the old one is finished: allowed
        classroom = Classroom.objects.get(semester=old)
        url = f'/api/admin/classrooms/{classroom.id}/'
        target = make_semester('Fourth', 'I', '2025-26')
        res = self.admin.put(url, {'name': 'Main', 'semester': str(target.id)}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (409, 'already_in_semester'))
        self.assertEqual(Classroom.objects.get(id=classroom.id).semester_id, old.id)  # not moved
        res = self.admin.put(url, {'name': 'Old group', 'semester': str(old.id)}, format='json')
        self.assertEqual(res.status_code, 200)  # renaming in place is fine

    def test_moving_an_older_class_group_to_a_finished_semester_is_allowed(self):
        # a is current in the active semester's Main group and in an extra group there
        lab = Classroom.objects.create(name='Lab', semester=self.current)
        StudentClassroom.objects.create(student=self.a, classroom=lab)
        finished = make_semester('First', 'II', '2024-25', is_active=False)
        res = self.admin.put(f'/api/admin/classrooms/{lab.id}/', {'name': 'Lab', 'semester': str(finished.id)},
                             format='json')
        self.assertEqual(res.status_code, 200, res.data)  # a finished semester is never current: no clash
        self.assertEqual(Classroom.objects.get(id=lab.id).semester_id, finished.id)

    def test_importing_into_a_semester_only_adds_new_accounts(self):
        # Students already in the portal are skipped ("exists"), so the import cannot make two
        from django.core.files.uploadedfile import SimpleUploadedFile
        content = ('student_id,name,email,level,term\n'
                   '2302001,Ayesha Rahman,a@example.com,Third,I\n'
                   '2302050,New Student,new@example.com,Second,I\n').encode()
        res = self.admin.post('/api/admin/students/import/', {
            'file': SimpleUploadedFile('students.csv', content, content_type='text/csv'),
            'apply': 'true', 'semester_id': str(self.other.id),
        }, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['summary'], {'create': 1, 'exists': 1, 'error': 0})
        self.assertEqual(self.members(self.other), {StudentProfile.objects.get(student_id=2302050).id})


class StudentProfileUnchangedByRosterTests(AdminTestCase):
    def test_adding_does_not_change_level(self):
        student = make_student('a@example.com', 2302001)
        semester = make_semester('Fourth', 'II')
        self.admin.post(f'/api/admin/semesters/{semester.id}/students/', {'profile_ids': [str(student.id)]},
                        format='json')
        self.assertEqual(StudentProfile.objects.get(pk=student.pk).current_level, 'Third')
