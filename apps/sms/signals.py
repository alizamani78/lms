from django.db.models.signals import post_save
from django.dispatch import receiver

from .notify import enqueue


@receiver(post_save, sender="attendance.AttendanceRecord")
def attendance_to_parents(sender, instance, created, **kwargs):
    if instance.status not in ("absent", "late"):
        return
    school = instance.classroom.school
    word = "غایب بود" if instance.status == "absent" else "با تأخیر حاضر شد"
    text = f"{instance.student.get_full_name() or instance.student.username} عزیز، امروز {instance.date.isoformat()} در کلاس {instance.classroom.name} {word}. — {school.name}"
    enqueue(instance.student, school, f"attendance_{instance.status}", text,
            f"att:{instance.pk}:{instance.status}")


@receiver(post_save, sender="coursework.Submission")
def homework_done_to_parents(sender, instance, created, **kwargs):
    if not created:
        return
    a = instance.assignment
    text = f"{instance.student.get_full_name() or instance.student.username} عزیز، تکلیف «{a.title}» درس {a.course.title} را انجام و ارسال کرد. — {a.course.org.name}"
    enqueue(instance.student, a.course.org, "homework_done", text, f"hw-done:{instance.pk}")
