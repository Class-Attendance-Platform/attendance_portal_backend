from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import IsAdminOrTeacher
from apps.academic.models import CourseInfo
from apps.reports.generators import export_csv, export_xlsx, export_pdf, export_docx


class ExportReportView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)

        fmt = request.query_params.get('export_format', 'xlsx').lower().strip()
        if fmt not in ('csv', 'xlsx', 'pdf', 'docx'):
            return Response(
                {'success': False, 'message': f'Unsupported format "{fmt}". Use csv, xlsx, pdf, or docx.'},
                status=400
            )

        date_str = request.query_params.get('date')
        filter_date = None
        if date_str:
            from datetime import date
            try:
                filter_date = date.fromisoformat(date_str)
            except ValueError:
                return Response(
                    {'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'},
                    status=400
                )

        generators = {
            'csv':  export_csv,
            'xlsx': export_xlsx,
            'pdf':  export_pdf,
            'docx': export_docx,
        }

        return generators[fmt](ci, filter_date)
