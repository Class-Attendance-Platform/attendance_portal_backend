from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from apps.users.models import StudentProfile, TeacherProfile, DeviceBinding
from apps.users.serializers import (
    RegisterSerializer,
    StudentProfileSerializer, StudentUpdateSerializer,
    TeacherProfileSerializer, TeacherUpdateSerializer,
)
from apps.users.permissions import IsAdmin

User = get_user_model()


# ── Pending Verification ──────────────────────────────────────────────────────

class PendingUsersView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        users = User.objects.filter(is_verified=False, deleted=False)
        data = [
            {'id': str(u.id), 'username': u.username, 'email': u.email, 'role': u.role}
            for u in users
        ]
        return Response({'success': True, 'users': data})


class VerifyUserView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, uuid):
        user = get_object_or_404(User, id=uuid, deleted=False)
        user.is_verified = True
        user.save()
        return Response({'success': True, 'message': f'{user.email} verified.'})


# ── Students ──────────────────────────────────────────────────────────────────

class AdminStudentListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        profiles = StudentProfile.objects.filter(user__deleted=False).select_related('user')
        serializer = StudentProfileSerializer(profiles, many=True)
        return Response({'success': True, 'students': serializer.data})

    def post(self, request):
        data = request.data.copy()
        data['role'] = 'STUDENT'
        serializer = RegisterSerializer(data=data)
        if serializer.is_valid():
            user = serializer.save()
            user.is_verified = True  # Admin-created users are pre-verified
            user.save()
            return Response(
                {'success': True, 'student': StudentProfileSerializer(user.student_profile).data},
                status=status.HTTP_201_CREATED
            )
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class AdminStudentDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        profile = get_object_or_404(StudentProfile, id=uuid, user__deleted=False)
        serializer = StudentUpdateSerializer(profile, data=request.data, partial=False)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'student': StudentProfileSerializer(profile).data})
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, uuid):
        profile = get_object_or_404(StudentProfile, id=uuid)
        profile.user.deleted = True
        profile.user.is_active = False
        profile.user.save()
        return Response({'success': True, 'message': 'Student deleted.'})


# ── Teachers ──────────────────────────────────────────────────────────────────

class AdminTeacherListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        profiles = TeacherProfile.objects.filter(user__deleted=False).select_related('user')
        serializer = TeacherProfileSerializer(profiles, many=True)
        return Response({'success': True, 'teachers': serializer.data})

    def post(self, request):
        data = request.data.copy()
        data['role'] = 'TEACHER'
        serializer = RegisterSerializer(data=data)
        if serializer.is_valid():
            user = serializer.save()
            user.is_verified = True
            user.save()
            return Response(
                {'success': True, 'teacher': TeacherProfileSerializer(user.teacher_profile).data},
                status=status.HTTP_201_CREATED
            )
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class AdminTeacherDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        profile = get_object_or_404(TeacherProfile, id=uuid, user__deleted=False)
        serializer = TeacherUpdateSerializer(profile, data=request.data, partial=False)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'teacher': TeacherProfileSerializer(profile).data})
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, uuid):
        profile = get_object_or_404(TeacherProfile, id=uuid)
        profile.user.deleted = True
        profile.user.is_active = False
        profile.user.save()
        return Response({'success': True, 'message': 'Teacher deleted.'})


# ── Device Binding ────────────────────────────────────────────────────────────

class AdminUnbindDeviceView(APIView):
    permission_classes = [IsAdmin]

    def delete(self, request, uuid):
        binding = get_object_or_404(DeviceBinding, id=uuid)
        binding.is_active = False
        binding.save()
        return Response({'success': True, 'message': 'Device unbound.'})
