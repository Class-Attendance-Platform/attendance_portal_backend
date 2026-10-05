"""
Guesses joined_at for class group memberships made before join dates were recorded
(API v2 section 9). Run once after the API v2 migrations.

Migration academic 0002 gave every existing membership joined_at = null ("from the start"),
so a student added to a semester mid-way under the old app would count as absent for every
class held before they were added. The old app wrote a log for every member each time a class
was saved, so a student's first log shows when they joined. For each membership with
joined_at null (only class dates before its left_at are looked at):
- their first log date in the class group's courses, if the group held classes before it;
- no log at all while the group held classes: its last class date (on the join day only a
  class with a log for them counts, so none of those classes count for them);
- otherwise nothing changes (they were there from the first class).
Limits: an old class re-saved later also logged the late joiners of that time, so a guess
can be earlier than the real join (it never hides a class they have a log for).

    python manage.py backfill_join_dates            # dry run: lists the changes
    python manage.py backfill_join_dates --apply    # writes them
"""
from collections import defaultdict
from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academic.models import CourseInfo, StudentClassroom
from apps.academic.stats import round_percent
from apps.attendance.models import AttendanceLog


@dataclass
class Guess:
    membership: StudentClassroom
    joined_at: object          # the guessed date
    held_before: int
    held_after: int
    attended: int

    def percent(self, held):
        return round_percent(self.attended * 100 / held) if held else None


def guessed_join_dates() -> list:
    """[Guess] for every membership with joined_at null whose guessed join date is later."""
    memberships = list(
        StudentClassroom.objects.filter(joined_at__isnull=True)
        .select_related('student__user', 'classroom__semester')
    )
    if not memberships:
        return []
    classroom_ids = {m.classroom_id for m in memberships}
    course_infos = list(CourseInfo.objects.filter(classroom_id__in=classroom_ids).select_related('course'))
    by_classroom = defaultdict(list)
    for ci in course_infos:
        by_classroom[ci.classroom_id].append(ci)

    logs = AttendanceLog.objects.filter(course_info__in=course_infos).order_by()
    dates = defaultdict(set)                    # course info id -> class dates
    for ci_id, day in logs.values_list('course_info_id', 'date').distinct():
        dates[ci_id].add(day)
    logged, present = defaultdict(set), defaultdict(set)   # (course info id, student id) -> dates
    for ci_id, student_id, day, status in (
        logs.filter(student_id__in={m.student_id for m in memberships})
        .values_list('course_info_id', 'student_id', 'date', 'status').distinct()
    ):
        logged[(ci_id, student_id)].add(day)
        if status == AttendanceLog.Status.PRESENT:
            present[(ci_id, student_id)].add(day)

    guesses = []
    for m in memberships:
        group = by_classroom[m.classroom_id]

        def before_left(days):
            return {d for d in days if m.left_at is None or d < m.left_at}

        group_dates = before_left(d for ci in group for d in dates[ci.id])
        if not group_dates:
            continue
        mine = before_left(d for ci in group for d in logged[(ci.id, m.student_id)])
        if mine and min(group_dates) >= min(mine):
            continue  # there from the first class
        joined_at = min(mine) if mine else max(group_dates)

        # What the student sees (courses not deleted): held before and after, attended
        guessed = StudentClassroom(joined_at=joined_at, left_at=m.left_at)
        held_before = held_after = attended = 0
        for ci in group:
            if ci.deleted or ci.course.deleted:
                continue
            key = (ci.id, m.student_id)
            for day in dates[ci.id]:
                if m.covers(day):
                    held_before += 1
                if guessed.counts_on(day, day in logged[key]):
                    held_after += 1
                    attended += day in present[key]
        guesses.append(Guess(m, joined_at, held_before, held_after, attended))

    guesses.sort(key=lambda g: (
        *g.membership.classroom.semester.sort_key(), g.membership.classroom.semester.session,
        g.membership.student.student_id,
    ))
    return guesses


def percent_text(value) -> str:
    return '-' if value is None else f'{value}%'


class Command(BaseCommand):
    help = 'Set joined_at for students added mid-semester before join dates were kept (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually write the join dates.')

    def handle(self, *args, **options):
        guesses = guessed_join_dates()
        if not guesses:
            self.stdout.write(self.style.SUCCESS('No late joiners without a join date: nothing to do.'))
            return

        self.stdout.write(f'Students who joined after their semester\'s first class: {len(guesses)}')
        for g in guesses:
            m = g.membership
            note = ' (deleted account)' if m.student.user.deleted else ''
            self.stdout.write(
                f'  {m.student.student_id} {m.student.user.get_full_name()}{note} · '
                f'{m.classroom.semester.label} ({m.classroom.name}): joined_at {g.joined_at.isoformat()}; '
                f'classes held {g.held_before} -> {g.held_after}, attended {g.attended}, '
                f'{percent_text(g.percent(g.held_before))} -> {percent_text(g.percent(g.held_after))}'
            )

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing changed. Add --apply to write the join dates.'))
            return

        with transaction.atomic():
            written = sum(
                StudentClassroom.objects.filter(id=g.membership.id, joined_at__isnull=True)
                .update(joined_at=g.joined_at)
                for g in guesses
            )
        self.stdout.write(self.style.SUCCESS(f'Join dates written: {written}.'))
