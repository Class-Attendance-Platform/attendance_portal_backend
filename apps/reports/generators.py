"""
Attendance report exports (API v2 section 8): CSV, XLSX, PDF and DOCX.

Each export_* function takes a CourseInfo and an optional date and returns an HttpResponse.
They all read one `Report` (build_report):
- rows = the course's class list (StudentClassroom.objects.class_list), by student id
- one date = one class: a student is present on a date if any log that day says PRESENT
- a cell outside the student's membership (joined_at <= date < left_at; on the join day a
  class held before they were added) is blank, not absent; held / attended / percent count
  only the class dates inside it (apps/academic/stats.py)
- times are local (Asia/Dhaka)
The PDF and DOCX split the date grid into pages that each repeat #, student ID, name and the
totals, so ID, name and % are always visible. The CSV stays a plain table (one header row).
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone

PRESENT = 'PRESENT'
ABSENT = 'ABSENT'
SHORT = {PRESENT: 'P', ABSENT: 'A', '': ''}

# Colours (hex without #)
HEADER_BG = '1F4E79'
PRESENT_BG = 'C6EFCE'
ABSENT_BG = 'FFC7CE'
LOW_TEXT = 'C00000'

# Landscape A4 grid (PDF and DOCX), in cm: fixed columns, one date column, usable width
PAGE_WIDTH_CM = 29.7
MARGIN_CM = 1.5
LEFT_COLUMNS_CM = [0.8, 1.9, 5.2]       # #, Student ID, Name
RIGHT_COLUMNS_CM = [1.1, 1.4, 1.5]      # Held, Present, %
DATE_COLUMN_CM = 0.95


def dates_per_page() -> int:
    """How many date columns fit next to the fixed columns on one landscape A4 page."""
    usable = PAGE_WIDTH_CM - 2 * MARGIN_CM
    free = usable - sum(LEFT_COLUMNS_CM) - sum(RIGHT_COLUMNS_CM)
    return max(1, int(free // DATE_COLUMN_CM))


def date_chunks(dates, size=None) -> list:
    """The report dates in page-sized groups (one empty group when there are no dates)."""
    size = size or dates_per_page()
    return [dates[i:i + size] for i in range(0, len(dates), size)] or [[]]


def chunk_caption(chunk, start, total) -> str:
    """"Classes 16–30 of 40: 01 Oct 2026 to 30 Oct 2026" above each part of a split grid."""
    return f'Classes {start + 1}–{start + len(chunk)} of {total}: {day_text(chunk[0])} to {day_text(chunk[-1])}'


# ── Shared data ───────────────────────────────────────────────────────────────

@dataclass
class ReportRow:
    number: int
    student_id: int
    name: str
    email: str
    cells: list            # one per report date: PRESENT | ABSENT | '' (not in the class that day)
    held: int
    attended: int
    percent: float = None  # None when nothing was held for them
    below_min: bool = False


@dataclass
class Report:
    course_code: str
    course_title: str
    semester_label: str
    semester_active: bool
    department: str
    teacher: str
    generated_at: datetime         # local (Asia/Dhaka)
    filter_date: date = None
    dates: list = field(default_factory=list)   # oldest first; with filter_date: [filter_date]
    class_on_date: bool = True                  # filter_date: was there a class that day?
    rows: list = field(default_factory=list)

    @property
    def title(self) -> str:
        return f'{self.course_code} — {self.course_title}'

    @property
    def semester_text(self) -> str:
        return self.semester_label if self.semester_active else f'{self.semester_label} (finished)'

    @property
    def generated_text(self) -> str:
        return f'{self.generated_at:%d %b %Y, %I:%M %p} (Dhaka time)'

    @property
    def scope_text(self) -> str:
        if self.filter_date:
            text = f'Date: {day_text(self.filter_date)}'
            return text if self.class_on_date else f'{text} (no class on this date)'
        if not self.dates:
            return 'Classes: none yet'
        return f'Classes: {len(self.dates)} ({day_text(self.dates[0])} to {day_text(self.dates[-1])})'

    def info_lines(self) -> list:
        return [
            f'Semester: {self.semester_text}',
            f'Department: {self.department}',
            f'Teacher: {self.teacher}',
            self.scope_text,
            f'Generated: {self.generated_text}',
        ]

    @property
    def legend(self) -> str:
        if self.filter_date:
            return 'Blank = not in the class on this date.'
        return (
            f'P = present, A = absent, blank = not in the class on that date. '
            f'Minimum attendance {settings.ATTENDANCE_MIN_PERCENT}%: lower percentages are in red.'
        )

    @property
    def running_header(self) -> str:
        return f'{self.title}  ·  {self.semester_label}  ·  {self.department}  ·  {self.teacher}'


def day_text(day) -> str:
    return f'{day:%d %b %Y}'


def percent_text(value) -> str:
    return '-' if value is None else f'{value}%'


def spreadsheet_text(value) -> str:
    """Text people typed (names), safe for Excel: a leading = + - @ would start a formula."""
    value = str(value)
    return f"'{value}" if value[:1] in ('=', '+', '-', '@', '\t', '\r') else value


def build_report(course_info, filter_date=None) -> Report:
    from apps.academic.serializers import person_name
    from apps.academic.stats import course_numbers
    from apps.attendance.models import AttendanceLog
    from apps.users.constants import DEPARTMENT_NAME, FACULTY_NAME

    numbers = course_numbers([course_info])[course_info.id]
    class_dates = sorted(numbers.class_dates)
    present = set(
        AttendanceLog.objects.filter(course_info=course_info, status=AttendanceLog.Status.PRESENT)
        .values_list('student_id', 'date').distinct()
    )

    report = Report(
        course_code=course_info.course.code,
        course_title=course_info.course.title,
        semester_label=course_info.semester.label,
        semester_active=course_info.semester.is_active,
        department=f'{FACULTY_NAME} ({DEPARTMENT_NAME})',
        teacher=person_name(course_info.teacher.user) if course_info.teacher_id else 'Not assigned',
        generated_at=timezone.localtime(),
        filter_date=filter_date,
        dates=[filter_date] if filter_date else class_dates,
        class_on_date=filter_date in class_dates if filter_date else True,
    )

    for number, (profile_id, n) in enumerate(numbers.students.items(), 1):
        membership = n.membership
        cells = []
        for day in report.dates:
            if not report.class_on_date or day not in n.dates:
                cells.append('')
            else:
                cells.append(PRESENT if (profile_id, day) in present else ABSENT)
        user = membership.student.user
        report.rows.append(ReportRow(
            number=number, student_id=membership.student.student_id, name=person_name(user),
            email=user.email, cells=cells, held=n.held, attended=n.attended, percent=n.percent,
            below_min=n.below_min,
        ))
    return report


# ── CSV ───────────────────────────────────────────────────────────────────────

def export_csv(course_info, filter_date=None):
    report = build_report(course_info, filter_date)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{_filename(course_info, filter_date, "csv")}"'

    writer = csv.writer(response)
    header = ['Student ID', 'Name', 'Email']
    if filter_date:
        header += ['Status']
    else:
        header += [d.isoformat() for d in report.dates] + ['Classes held', 'Present', 'Percentage']
    writer.writerow(header)

    for row in report.rows:
        values = [row.student_id, spreadsheet_text(row.name), spreadsheet_text(row.email)] + row.cells
        if not filter_date:
            values += [row.held, row.attended, '' if row.percent is None else f'{row.percent}%']
        writer.writerow(values)
    return response


# ── XLSX ──────────────────────────────────────────────────────────────────────

def export_xlsx(course_info, filter_date=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    report = build_report(course_info, filter_date)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Attendance'

    header_font = Font(bold=True, color='FFFFFF', size=10)
    header_fill = PatternFill(fill_type='solid', fgColor=HEADER_BG)
    fills = {
        PRESENT: PatternFill(fill_type='solid', fgColor=PRESENT_BG),
        ABSENT: PatternFill(fill_type='solid', fgColor=ABSENT_BG),
    }
    center = Alignment(horizontal='center', vertical='center')
    left = Alignment(horizontal='left', vertical='center')
    thin = Side(style='thin', color='999999')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Header block: course, semester, department, teacher, dates, generated (Dhaka time)
    ws.cell(row=1, column=1, value=spreadsheet_text(report.title)).font = Font(bold=True, size=13, color=HEADER_BG)
    lines = report.info_lines() + [report.legend]
    for offset, text in enumerate(lines, 2):
        ws.cell(row=offset, column=1, value=spreadsheet_text(text)).font = Font(size=10, color='444444')
    header_row = len(lines) + 3

    headers = ['#', 'Student ID', 'Name', 'Email']
    if filter_date:
        headers += ['Status']
    else:
        headers += [day_text(d) for d in report.dates] + ['Held', 'Present', 'Percent']
    first_date_col = 5
    for col, text in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.font, cell.fill, cell.border = header_font, header_fill, border
        is_date = not filter_date and first_date_col <= col < first_date_col + len(report.dates)
        cell.alignment = Alignment(horizontal='center', vertical='center', text_rotation=90 if is_date else 0)
    if not filter_date and report.dates:
        ws.row_dimensions[header_row].height = 70

    for index, row in enumerate(report.rows):
        r = header_row + 1 + index
        texts = [spreadsheet_text(row.name), spreadsheet_text(row.email)]
        for col, value in enumerate([row.number, row.student_id] + texts, 1):
            cell = ws.cell(row=r, column=col, value=value)
            cell.border, cell.alignment = border, (left if col in (3, 4) else center)
        if filter_date:
            status = row.cells[0]
            cell = ws.cell(row=r, column=5, value=status.capitalize())
            cell.border, cell.alignment = border, center
            if status:
                cell.fill = fills[status]
            continue
        for d_index, status in enumerate(row.cells):
            cell = ws.cell(row=r, column=first_date_col + d_index, value=SHORT[status])
            cell.border, cell.alignment = border, center
            if status:
                cell.fill = fills[status]
        col = first_date_col + len(report.dates)
        for value in (row.held, row.attended):
            cell = ws.cell(row=r, column=col, value=value)
            cell.border, cell.alignment = border, center
            col += 1
        cell = ws.cell(row=r, column=col, value=None if row.percent is None else round(row.percent / 100, 3))
        cell.number_format = '0.0%'
        cell.border, cell.alignment = border, center
        if row.below_min:
            cell.font = Font(bold=True, color=LOW_TEXT)

    # Column widths from the table only (the header block may overflow to the right)
    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        if not filter_date and first_date_col <= col < first_date_col + len(report.dates):
            ws.column_dimensions[letter].width = 4.5
            continue
        values = [ws.cell(row=r, column=col).value for r in range(header_row, header_row + 1 + len(report.rows))]
        ws.column_dimensions[letter].width = min(max((len(str(v or '')) for v in values), default=6) + 3, 40)
    # Keep #, ID, name and email in view while scrolling through the dates
    ws.freeze_panes = ws.cell(row=header_row + 1, column=first_date_col)

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{_filename(course_info, filter_date, "xlsx")}"'
    return response


# ── PDF ───────────────────────────────────────────────────────────────────────

def export_pdf(course_info, filter_date=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    report = build_report(course_info, filter_date)
    page_width, page_height = landscape(A4)
    margin = MARGIN_CM * cm

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=(page_width, page_height),
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
        title=f'{report.course_code} attendance', author='HSTU Attendance Portal',
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('ReportTitle', parent=styles['Title'], fontSize=15, leading=18,
                                 spaceAfter=4, alignment=0)
    info_style = ParagraphStyle('ReportInfo', parent=styles['Normal'], fontSize=9, leading=12,
                                textColor=colors.HexColor('#333333'))
    note_style = ParagraphStyle('ReportNote', parent=info_style, fontSize=8, leading=10,
                                textColor=colors.HexColor('#555555'))
    name_style = ParagraphStyle('Name', parent=styles['Normal'], fontSize=8, leading=9.5)
    fill = {PRESENT: colors.HexColor(f'#{PRESENT_BG}'), ABSENT: colors.HexColor(f'#{ABSENT_BG}')}
    base_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(f'#{HEADER_BG}')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7.5),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (2, 1), (2, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]

    def name_cell(row):
        return Paragraph(escape(row.name), name_style)

    def one_date_table():
        """#, ID, name, email and the status on the chosen date."""
        data = [['#', 'Student ID', 'Name', 'Email', 'Status']]
        cell_styles = [('ALIGN', (3, 1), (3, -1), 'LEFT')]
        for i, row in enumerate(report.rows, 1):
            status = row.cells[0]
            data.append([row.number, row.student_id, name_cell(row), row.email, status.capitalize()])
            if status:
                cell_styles.append(('BACKGROUND', (4, i), (4, i), fill[status]))
        table = Table(data, colWidths=[w * cm for w in (0.9, 2.4, 8.0, 10.4, 2.8)], repeatRows=1)
        table.setStyle(TableStyle(base_style + cell_styles))
        return table

    def grid_table(chunk, start):
        """One page wide: #, ID, name, this group of dates, held, present, % (rows may continue)."""
        offset = len(LEFT_COLUMNS_CM)
        data = [['#', 'Student ID', 'Name'] + [f'{d:%d}\n{d:%b}' for d in chunk] + ['Held', 'Present', '%']]
        cell_styles = []
        for i, row in enumerate(report.rows, 1):
            statuses = row.cells[start:start + len(chunk)]
            data.append([row.number, row.student_id, name_cell(row)] + [SHORT[s] for s in statuses]
                        + [row.held, row.attended, percent_text(row.percent)])
            for j, status in enumerate(statuses):
                if status:
                    cell_styles.append(('BACKGROUND', (offset + j, i), (offset + j, i), fill[status]))
            if row.below_min:
                cell_styles += [('TEXTCOLOR', (-1, i), (-1, i), colors.HexColor(f'#{LOW_TEXT}')),
                                ('FONTNAME', (-1, i), (-1, i), 'Helvetica-Bold')]
        widths = LEFT_COLUMNS_CM + [DATE_COLUMN_CM] * len(chunk) + RIGHT_COLUMNS_CM
        table = Table(data, colWidths=[w * cm for w in widths], repeatRows=1)
        table.setStyle(TableStyle(base_style + cell_styles))
        return table

    story = [Paragraph(escape(report.title), title_style)]
    story += [Paragraph(escape(line), info_style) for line in report.info_lines()]
    story += [Spacer(1, 0.15 * cm), Paragraph(escape(report.legend), note_style), Spacer(1, 0.3 * cm)]
    if filter_date:
        story.append(one_date_table())
    else:
        chunks = date_chunks(report.dates)
        start = 0
        for part, chunk in enumerate(chunks):
            if part:
                story.append(PageBreak())
            if len(chunks) > 1:
                story += [Paragraph(escape(chunk_caption(chunk, start, len(report.dates))), info_style),
                          Spacer(1, 0.15 * cm)]
            story.append(grid_table(chunk, start))
            start += len(chunk)
    if not report.rows:
        story += [Spacer(1, 0.3 * cm), Paragraph('No students in this course yet.', info_style)]

    def footer(canvas):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.HexColor('#777777'))
        canvas.drawString(margin, 0.8 * cm, f'HSTU Attendance Portal  ·  Generated {report.generated_text}')
        canvas.drawRightString(page_width - margin, 0.8 * cm, f'Page {canvas.getPageNumber()}')
        canvas.restoreState()

    def first_page(canvas, _doc):
        footer(canvas)

    def later_pages(canvas, _doc):
        # Course, semester, department and teacher on every page after the first
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#555555'))
        text, max_width = report.running_header, page_width - 2 * margin
        if canvas.stringWidth(text, 'Helvetica', 8) > max_width:
            while text and canvas.stringWidth(text + '…', 'Helvetica', 8) > max_width:
                text = text[:-1]
            text = text.rstrip() + '…'
        canvas.drawString(margin, page_height - 1.0 * cm, text)
        canvas.restoreState()
        footer(canvas)

    doc.build(story, onFirstPage=first_page, onLaterPages=later_pages)

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{_filename(course_info, filter_date, "pdf")}"'
    return response


# ── DOCX ──────────────────────────────────────────────────────────────────────

def export_docx(course_info, filter_date=None):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Pt, RGBColor

    report = build_report(course_info, filter_date)
    doc = Document()
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(9)

    # Landscape A4; the running header and footer repeat on every page
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(PAGE_WIDTH_CM), Cm(21.0)
    section.left_margin = section.right_margin = Cm(MARGIN_CM)
    section.top_margin = section.bottom_margin = Cm(MARGIN_CM)
    section.header.paragraphs[0].text = report.running_header
    section.footer.paragraphs[0].text = f'HSTU Attendance Portal · Generated {report.generated_text}'
    for part in (section.header, section.footer):
        for run in part.paragraphs[0].runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    title = doc.add_heading(level=1)
    title_run = title.add_run(report.title)
    title_run.font.color.rgb = RGBColor.from_string(HEADER_BG)
    for line in report.info_lines():
        para = doc.add_paragraph(line)
        para.paragraph_format.space_after = Pt(0)
    legend = doc.add_paragraph()
    legend_run = legend.add_run(report.legend)
    legend_run.font.size = Pt(8)
    legend_run.italic = True

    if filter_date:
        widths = [0.9, 2.4, 8.0, 10.4, 2.8]
        table = _docx_table(doc, ['#', 'Student ID', 'Name', 'Email', 'Status'], widths)
        for row in report.rows:
            status = row.cells[0]
            values = [row.number, row.student_id, row.name, row.email, status.capitalize()]
            cells = _docx_row(table, values, widths, left_columns=(2, 3))
            if status:
                _set_cell_bg(cells[4], PRESENT_BG if status == PRESENT else ABSENT_BG)
    else:
        chunks = date_chunks(report.dates)
        offset = len(LEFT_COLUMNS_CM)
        start = 0
        for part, chunk in enumerate(chunks):
            if part:
                doc.add_page_break()
            if len(chunks) > 1:
                doc.add_paragraph(chunk_caption(chunk, start, len(report.dates)))
            headers = ['#', 'Student ID', 'Name'] + [f'{d:%d %b}' for d in chunk] + ['Held', 'Present', '%']
            widths = LEFT_COLUMNS_CM + [DATE_COLUMN_CM] * len(chunk) + RIGHT_COLUMNS_CM
            table = _docx_table(doc, headers, widths)
            for row in report.rows:
                statuses = row.cells[start:start + len(chunk)]
                values = ([row.number, row.student_id, row.name] + [SHORT[s] for s in statuses]
                          + [row.held, row.attended, percent_text(row.percent)])
                cells = _docx_row(table, values, widths, left_columns=(2,))
                for j, status in enumerate(statuses):
                    if status:
                        _set_cell_bg(cells[offset + j], PRESENT_BG if status == PRESENT else ABSENT_BG)
                if row.below_min:
                    run = cells[-1].paragraphs[0].runs[0]
                    run.font.bold = True
                    run.font.color.rgb = RGBColor.from_string(LOW_TEXT)
            start += len(chunk)

    if not report.rows:
        doc.add_paragraph('No students in this course yet.')

    buffer = io.BytesIO()
    doc.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    response['Content-Disposition'] = f'attachment; filename="{_filename(course_info, filter_date, "docx")}"'
    return response


def _docx_table(doc, headers, widths_cm):
    """A grid table with a coloured header row that repeats on every page."""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    header = table.rows[0]
    repeat = OxmlElement('w:tblHeader')
    repeat.set(qn('w:val'), 'true')
    header._tr.get_or_add_trPr().append(repeat)
    for cell, text, width in zip(header.cells, headers, widths_cm):
        cell.width = Cm(width)
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run(str(text))
        run.font.bold = True
        run.font.size = Pt(7.5)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _set_cell_bg(cell, HEADER_BG)
    return table


def _docx_row(table, values, widths_cm, left_columns=()):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    cells = table.add_row().cells
    for i, (cell, value, width) in enumerate(zip(cells, values, widths_cm)):
        cell.width = Cm(width)
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT if i in left_columns else WD_ALIGN_PARAGRAPH.CENTER
        para.add_run(str(value)).font.size = Pt(8)
    return cells


# ── Helpers ───────────────────────────────────────────────────────────────────

def _filename(course_info, filter_date, ext):
    code = course_info.course.code.replace(' ', '_')
    if filter_date:
        return f'{code}_attendance_{filter_date}.{ext}'
    return f'{code}_attendance_full.{ext}'


def _set_cell_bg(cell, hex_color: str):
    """Set background color of a docx table cell."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tc_pr.append(shd)
