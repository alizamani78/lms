from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ClassSession


@receiver(post_save, sender=ClassSession)
def enqueue_room_creation(sender, instance, created, **kwargs):
    if created and instance.auto_create:
        from .tasks import create_remote_meeting
        transaction.on_commit(lambda: create_remote_meeting.delay(instance.pk))
