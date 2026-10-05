import uuid

from django.db import models


class StudentFace(models.Model):
    """
    One registered face photo of a student (3 per student: straight, left, right).
    Keeps a small aligned face crop (so admins can check who registered) and the
    512-number fingerprint used for matching. Class photos are never stored.
    """
    class Pose(models.TextChoices):
        STRAIGHT = 'STRAIGHT', 'Straight'
        LEFT = 'LEFT', 'Left'
        RIGHT = 'RIGHT', 'Right'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('users.StudentProfile', on_delete=models.CASCADE, related_name='faces')
    pose = models.CharField(max_length=10, choices=Pose.choices)
    crop_jpeg = models.BinaryField()     # aligned 112x112 face, JPEG bytes
    embedding = models.BinaryField()     # 512 float32 values
    engine = models.CharField(max_length=50)  # which model made the embedding
    consented_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['student', 'pose']
        unique_together = ('student', 'pose')

    def __str__(self):
        return f'{self.student} [{self.pose}]'
