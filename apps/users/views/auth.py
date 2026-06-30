from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from apps.users.serializers import RegisterSerializer, UserMeSerializer

User = get_user_model()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Embed role in JWT payload."""
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role
        token['email'] = user.email
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data['success'] = True

        student_profile = None
        if user.role == User.Role.STUDENT and hasattr(user, "student_profile"):
            p = user.student_profile
            student_profile = {
                "id": str(p.id),
                "student_id": p.student_id,
                "current_level": p.current_level,
                "current_semester": p.current_semester,
                "hardware_finger_id": p.hardware_finger_id,
            }

        teacher_profile = None
        if user.role == User.Role.TEACHER and hasattr(user, "teacher_profile"):
            p = user.teacher_profile
            teacher_profile = {"id": str(p.id), "employee_id": p.employee_id}

        data['user'] = {
            'id': str(user.id),
            'userName': user.get_full_name() or user.username,
            'email': user.email,
            'role': user.role.upper(),
            'faculty': user.faculty,
            'department': user.department,
            'is_verified': user.is_verified,
            'student_profile': student_profile,
            'teacher_profile': teacher_profile,
        }
        return data


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            # Embed role in token
            refresh['role'] = user.role

            student_profile = None
            if user.role == User.Role.STUDENT and hasattr(user, "student_profile"):
                p = user.student_profile
                student_profile = {
                    "id": str(p.id),
                    "student_id": p.student_id,
                    "current_level": p.current_level,
                    "current_semester": p.current_semester,
                    "hardware_finger_id": p.hardware_finger_id,
                }

            teacher_profile = None
            if user.role == User.Role.TEACHER and hasattr(user, "teacher_profile"):
                p = user.teacher_profile
                teacher_profile = {"id": str(p.id), "employee_id": p.employee_id}

            return Response({
                'success': True,
                'user': {
                    'id': str(user.id),
                    'userName': user.get_full_name() or user.username,
                    'email': user.email,
                    'role': user.role.upper(),
                    'faculty': user.faculty,
                    'department': user.department,
                    'is_verified': user.is_verified,
                    'student_profile': student_profile,
                    'teacher_profile': teacher_profile,
                },
                'tokens': {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                }
            }, status=status.HTTP_201_CREATED)
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserMeSerializer(request.user)
        return Response({'success': True, 'user': serializer.data})
