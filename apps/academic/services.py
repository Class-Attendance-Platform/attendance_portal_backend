from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.academic.models import Classroom, Semester, StudentClassroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.users.models import StudentProfile

MAIN_CLASSROOM = 'Main'

def main_classroom(semester, create=True):
    classrooms = semester.classrooms.filter(deleted=False)
    classroom = classrooms.filter(name=MAIN_CLASSROOM).first() or classrooms.order_by('name').first()
    if classroom is None and create:
        classroom = Classroom.objects.create(name=MAIN_CLASSROOM, semester=semester)
    return classroom

def main_classrooms(semesters) -> dict:
    result = {}
    for classroom in Classroom.objects.filter(semester__in=semesters, deleted=False).order_by('name'):
        chosen = result.get(classroom.semester_id)
        if chosen is None or (classroom.name == MAIN_CLASSROOM and chosen.name != MAIN_CLASSROOM):
            result[classroom.semester_id] = classroom
    return result

def level_has_active_semester(level, exclude=None) -> bool:
    others = Semester.objects.filter(level=level, is_active=True, deleted=False)
    if exclude is not None:
        others = others.exclude(id=exclude.id)
    return others.exists()

def create_semester(level, term, session, start_date=None, end_date=None) -> Semester:
    with transaction.atomic():
        semester = Semester.objects.create(
            level=level, semester=term, session=session, start_date=start_date, end_date=end_date,
        )
        main_classroom(semester)
    return semester

def join_date(semester, today=None):
    if AttendanceLog.objects.filter(course_info__semester=semester).exists():
        return today or timezone.localdate()
    return None

def leave_date(classroom, today=None):
    today = today or timezone.localdate()
    held_today = (
        AttendanceLog.objects.filter(course_info__classroom=classroom, date=today).exists()
        or AttendanceSession.objects.filter(course_info__classroom=classroom, date=today).exists()
    )
    return today + timedelta(days=1) if held_today else today

class AlreadyInSemester(Exception):
    def __init__(self, memberships):
        super().__init__('Some students are already in another semester.')
        self.memberships = memberships

def check_one_semester(profile_ids, exclude=(), moving=None):
    profile_ids = list(profile_ids)
    list(StudentProfile.objects.select_for_update().filter(id__in=profile_ids).order_by('pk'))
    clashes = (
        StudentClassroom.objects.current().filter(
            student_id__in=profile_ids, classroom__deleted=False,
            classroom__semester__is_active=True, classroom__semester__deleted=False,
        ).exclude(classroom__semester__in=[s.pk for s in exclude])
    )
    if moving is not None:
        clashes = clashes.exclude(classroom=moving)
    clashes = list(clashes.select_related('student__user', 'classroom__semester'))
    if clashes:
        raise AlreadyInSemester(clashes)

def add_members(classroom, profiles, joined_at) -> dict:
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
    if classroom is None:
        return 0
    return StudentClassroom.objects.filter(
        classroom=classroom, student_id__in=profile_ids,
    ).current().update(left_at=leave_date(classroom, today))

def promote(source, target, profile_ids=None, today=None) -> int:
    today = today or timezone.localdate()
    with transaction.atomic():
        source_room = main_classroom(source)
        members = StudentClassroom.objects.filter(classroom=source_room).current().active_accounts()
        if profile_ids is not None:
            members = members.filter(student_id__in=profile_ids)
        members = list(members.select_related('student'))
        profiles = [m.student for m in members]

        check_one_semester([p.id for p in profiles], exclude=[source, target])
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
