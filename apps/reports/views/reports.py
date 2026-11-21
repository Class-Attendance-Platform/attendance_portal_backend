from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.academic.views.teacher import teacher_course
from apps.attendance.records import parse_day
from apps.attendance.services import finalize_expired_sessions
from apps.reports.generators import export_csv, export_docx, export_pdf, export_xlsx
from apps.users.permissions import IsAdminOrTeacher
from config.errors import error_response

GENERATORS = {
    'csv': export_csv,
    'xlsx': export_xlsx,
    'pdf': export_pdf,
    'docx': export_docx,
}

class FileTypeNegotiation(DefaultContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        renderer = renderers[0]
        return renderer, renderer.media_type

class ExportReportView(APIView):
    permission_classes = [IsAdminOrTeacher]
    renderer_classes = [JSONRenderer]
    content_negotiation_class = FileTypeNegotiation

    def get(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error

        params = request.query_params
        fmt = (params.get('format') or params.get('export_format') or 'xlsx').lower().strip()
        if fmt not in GENERATORS:
            return error_response(
                f'Unsupported format "{fmt}". Use pdf, xlsx, csv or docx.', status=400, code='invalid_format',
            )

        filter_date = None
        if params.get('date'):
            filter_date = parse_day(params['date'])
            if filter_date is None:
                return error_response('Invalid date. Use YYYY-MM-DD.', status=400, code='invalid_date')

        finalize_expired_sessions(ci)
        return GENERATORS[fmt](ci, filter_date)
