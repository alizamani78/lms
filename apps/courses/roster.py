"""فهرست دانش‌آموزان یک درس: کسانی که به درس ثبت شده‌اند، و همه‌ی دانش‌آموزان کلاس همان درس.

نمره‌ی سرکلاسی، ارفاق و فهرست دانش‌آموزان درس همه از همین فهرست استفاده می‌کنند تا با هم یکی باشند.
"""


def roster_ids(course):
    ids = set(course.enrollments.values_list("student_id", flat=True))
    if course.classroom_id:
        ids |= set(course.classroom.students.values_list("pk", flat=True))
    return ids
