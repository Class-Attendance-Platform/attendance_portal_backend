import uuid

from django.db import models

class StudentFace(models.Model):
    class Pose(models.TextChoices):
        STRAIGHT = 'STRAIGHT', 'Straight'
        LEFT = 'LEFT', 'Left'
        RIGHT = 'RIGHT', 'Right'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('users.StudentProfile', on_delete=models.CASCADE, related_name='faces')
    pose = models.CharField(max_length=10, choices=Pose.choices)
    crop_jpeg = models.BinaryField()
    embedding = models.BinaryField()
    engine = models.CharField(max_length=50)
    consented_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['student', 'pose']
        unique_together = ('student', 'pose')

    def __str__(self):
        return f'{self.student} [{self.pose}]'
