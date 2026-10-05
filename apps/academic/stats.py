"""
Attendance numbers for courses and semesters. One date = one class: a student counts as
present on a date if any session that day marked them present.

For each student on a course's class list (StudentClassroom.objects.class_list: current
members of an active semester; everyone who was in a finished one):
- held = the course's class dates inside their membership (joined_at <= date < left_at); on
  the join day only if they have a log that day (StudentClassroom.counts_on: a class held
  before they were added does not count for them)
- attended = those dates on which they were present
- percent = attended / held * 100 (None when held = 0: never "low" without classes)
"""
from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings

from apps.academic.models import Classroom, CourseInfo, StudentClassroom
from apps.attendance.models import AttendanceLog


def round_percent(value):
    return None if value is None else round(value, 1)


@dataclass
class StudentNumbers:
    membership: StudentClassroom
    attended: int = 0
    held: int = 0
    dates: frozenset = frozenset()   # the held dates

    @property
    def percent(self):
        return round_percent(self.attended * 100 / self.held) if self.held else None

    @property
    def below_min(self) -> bool:
        return self.held > 0 and self.attended * 100 < settings.ATTENDANCE_MIN_PERCENT * self.held


@dataclass
class CourseNumbers:
    class_dates: list = field(default_factory=list)   # newest first
    students: dict = field(default_factory=dict)      # profile id -> StudentNumbers

    @property
    def student_count(self) -> int:
        return len(self.students)

    @property
    def classes_held(self) -> int:
        return len(self.class_dates)

    @property
    def average_percent(self):
        percents = [n.attended * 100 / n.held for n in self.students.values() if n.held]
        return round_percent(sum(percents) / len(percents)) if percents else None

    @property
    def below_min_count(self) -> int:
        return sum(n.below_min for n in self.students.values())


def course_numbers(course_infos) -> dict:
    """{course info id: CourseNumbers} for many courses in a fixed number of queries."""
    course_infos = list(course_infos)
    ids = [ci.id for ci in course_infos]
    classroom_ids = {ci.classroom_id for ci in course_infos}

    active = dict(Classroom.objects.filter(id__in=classroom_ids).values_list('id', 'semester__is_active'))
    class_lists = defaultdict(list)
    memberships = (
        StudentClassroom.objects.filter(classroom_id__in=classroom_ids).active_accounts()
        .select_related('student__user').order_by('student__student_id')
    )
    for membership in memberships:
        if membership.left_at is None or not active.get(membership.classroom_id, True):
            class_lists[membership.classroom_id].append(membership)

    logs = AttendanceLog.objects.filter(course_info_id__in=ids).order_by()
    dates = defaultdict(set)
    for ci_id, day in logs.values_list('course_info_id', 'date').distinct():
        dates[ci_id].add(day)
    present = defaultdict(set)
    for ci_id, student_id, day in (
        logs.filter(status=AttendanceLog.Status.PRESENT).values_list('course_info_id', 'student_id', 'date').distinct()
    ):
        present[(ci_id, student_id)].add(day)
    # Who has a log on their join day (a class held before they were added that day has none)
    join_days = {m.joined_at for ms in class_lists.values() for m in ms if m.joined_at}
    logged = set()
    if join_days:
        joiners = {m.student_id for ms in class_lists.values() for m in ms if m.joined_at}
        logged = set(
            logs.filter(student_id__in=joiners, date__in=join_days)
            .values_list('course_info_id', 'student_id', 'date').distinct()
        )

    result = {}
    for ci in course_infos:
        numbers = CourseNumbers(class_dates=sorted(dates[ci.id], reverse=True))
        for membership in class_lists[ci.classroom_id]:
            held = [
                d for d in numbers.class_dates
                if membership.counts_on(d, (ci.id, membership.student_id, d) in logged)
            ]
            here = present.get((ci.id, membership.student_id), set())
            numbers.students[membership.student_id] = StudentNumbers(
                membership=membership, held=len(held), attended=sum(d in here for d in held),
                dates=frozenset(held),
            )
        result[ci.id] = numbers
    return result


def semester_numbers(semesters) -> dict:
    """
    {semester id: {average_percent, below_min_count}} over the semester's courses:
    average_percent = mean of every (student, course) percent; below_min_count = students
    below the minimum in at least one course.
    """
    course_infos = list(
        CourseInfo.objects.filter(semester__in=semesters, deleted=False, course__deleted=False)
    )
    numbers = course_numbers(course_infos)
    percents, below = defaultdict(list), defaultdict(set)
    for ci in course_infos:
        for profile_id, n in numbers[ci.id].students.items():
            if n.held:
                percents[ci.semester_id].append(n.attended * 100 / n.held)
            if n.below_min:
                below[ci.semester_id].add(profile_id)
    return {
        semester.id: {
            'average_percent': round_percent(sum(percents[semester.id]) / len(percents[semester.id]))
            if percents[semester.id] else None,
            'below_min_count': len(below[semester.id]),
        }
        for semester in semesters
    }
