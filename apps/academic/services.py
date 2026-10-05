"""
Semesters, their hidden class group and its members (API v2 section 3).

A semester has one class group: its "Main" classroom, made with the semester. Members are
StudentClassroom rows covering joined_at <= day < left_at (joined_at null = from the start,
left_at null = still a member). Removing or promoting a student sets left_at and keeps the
row, so their attendance history stays visible.

Same-day changes: the day someone joins or leaves is split around the classes already held
that day. Joining: joined_at = today, and a class held before they were added does not count
for them (StudentClassroom.counts_on). Leaving: left_at = tomorrow when the class group
already held (or is holding) a class today, so that class still counts (leave_date).
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.academic.models import Classroom, Semester, StudentClassroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.users.models import StudentProfile

MAIN_CLASSROOM = 'Main'


def main_classroom(semester, create=True):
    """The semester's class group ("Main", else its first one); made if missing and `create`."""
    classrooms = semester.classrooms.filter(deleted=False)
    classroom = classrooms.filter(name=MAIN_CLASSROOM).first() or classrooms.order_by('name').first()
    if classroom is None and create:
        classroom = Classroom.objects.create(name=MAIN_CLASSROOM, semester=semester)
    return classroom


def main_classrooms(semesters) -> dict:
    """{semester id: its class group} for many semesters in one query (nothing is made)."""
    result = {}
    for classroom in Classroom.objects.filter(semester__in=semesters, deleted=False).order_by('name'):
        chosen = result.get(classroom.semester_id)
        if chosen is None or (classroom.name == MAIN_CLASSROOM and chosen.name != MAIN_CLASSROOM):
            result[classroom.semester_id] = classroom
    return result


def level_has_active_semester(level, exclude=None) -> bool:
    """Another active (not deleted) semester of this level exists."""
    others = Semester.objects.filter(level=level, is_active=True, deleted=False)
    if exclude is not None:
        others = others.exclude(id=exclude.id)
    return others.exists()


def create_semester(level, term, session, start_date=None, end_date=None) -> Semester:
    """A new active semester with its class group."""
    with transaction.atomic():
        semester = Semester.objects.create(
            level=level, semester=term, session=session, start_date=start_date, end_date=end_date,
        )
        main_classroom(semester)
    return semester


def join_date(semester, today=None):
    """
    joined_at for a student added now: today once the semester has held a class (a late
    joiner counts from today; a class held earlier today does not count for them, see
    StudentClassroom.counts_on); before its first class, null (= from the start), so classes
    entered later for earlier dates still count for them.
    """
    if AttendanceLog.objects.filter(course_info__semester=semester).exists():
        return today or timezone.localdate()
    return None


def leave_date(classroom, today=None):
    """
    left_at for a student removed or promoted now: tomorrow when the class group already
    held a class today or has a session today (a live one included), so today's attendance
    stays counted and a running session still saves their check-in; otherwise today. Either
    way they stop being current at once (no check-in, live sessions or face matching); until
    the day ends, a later class that day still logs them (one date = one class).
    """
    today = today or timezone.localdate()
    held_today = (
        AttendanceLog.objects.filter(course_info__classroom=classroom, date=today).exists()
        or AttendanceSession.objects.filter(course_info__classroom=classroom, date=today).exists()
    )
    return today + timedelta(days=1) if held_today else today


def add_members(classroom, profiles, joined_at) -> dict:
    """
    Adds students to a class group. A former member comes back with left_at cleared and
    the original joined_at kept; a current member is left as is.
    """
    profiles = list({p.id: p for p in profiles}.values())
    existing = {
        m.student_id: m for m in StudentClassroom.objects.filter(classroom=classroom, student__in=profiles)
    }
    new, rejoined, already = [], [], 0
    for profile in profiles:
        membership = existing.get(profile.id)
        if membership is None:
            new.append(StudentClassroom(student=profile, classroom=classroom, joined_at=joined_at))
        elif membership.left_at is not None:
            rejoined.append(membership.id)
        else:
            already += 1
    StudentClassroom.objects.bulk_create(new)
    StudentClassroom.objects.filter(id__in=rejoined).update(left_at=None)
    return {'added': len(new), 'rejoined': len(rejoined), 'already_in': already}


def remove_members(classroom, profile_ids, today=None) -> int:
    """Marks current members as left (leave_date; their rows and history stay)."""
    if classroom is None:
        return 0
    return StudentClassroom.objects.filter(
        classroom=classroom, student_id__in=profile_ids,
    ).current().update(left_at=leave_date(classroom, today))


def promote(source, target, profile_ids=None, today=None) -> int:
    """
    Moves current members of `source` (all, or only `profile_ids`) to `target`: left_at =
    leave_date on the source (today, or tomorrow after a class today), joined on the target,
    profile level/term set to the target's. The source semester is finished (never deleted).
    Returns how many students moved.
    """
    today = today or timezone.localdate()
    with transaction.atomic():
        source_room = main_classroom(source)
        members = StudentClassroom.objects.filter(classroom=source_room).current().active_accounts()
        if profile_ids is not None:
            members = members.filter(student_id__in=profile_ids)
        members = list(members.select_related('student'))
        profiles = [m.student for m in members]

        add_members(main_classroom(target), profiles, join_date(target, today))
        StudentClassroom.objects.filter(id__in=[m.id for m in members]).update(
            left_at=leave_date(source_room, today),
        )
        StudentProfile.objects.filter(id__in=[p.id for p in profiles]).update(
            current_level=target.level, current_semester=target.semester,
        )
        if source.is_active:
            source.is_active = False
            source.save(update_fields=['is_active'])
    return len(profiles)
