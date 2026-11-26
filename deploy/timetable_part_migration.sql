-- مهاجرت گام اول: فیلد «نیمه‌ی زنگ» (part) و قید یکتایی جدید برای خانه‌های برنامه.
-- برنامه‌های موجود همه part=0 می‌گیرند و هیچ داده‌ای حذف نمی‌شود.
-- قبل از اجرا حتماً پشتیبان بگیرید: deploy/timetable_part_migration.sh این کار را انجام می‌دهد.
BEGIN;

ALTER TABLE timetable_timetableentry
    ADD COLUMN IF NOT EXISTS part smallint NOT NULL DEFAULT 0;

ALTER TABLE timetable_timetableentry
    ADD COLUMN IF NOT EXISTS piece_hours smallint NOT NULL DEFAULT 0;

ALTER TABLE timetable_timetableentry
    ADD COLUMN IF NOT EXISTS extra_day boolean NOT NULL DEFAULT false;

ALTER TABLE timetable_timetableentry
    DROP CONSTRAINT IF EXISTS uniq_entry_class_slot;

ALTER TABLE timetable_timetableentry
    DROP CONSTRAINT IF EXISTS uniq_entry_class_slot_part;

ALTER TABLE timetable_timetableentry
    ADD CONSTRAINT uniq_entry_class_slot_part UNIQUE (version_id, classroom_id, weekday, period_id, part);

-- بررسی: هیچ ردیفی با part نامعتبر نباشد
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM timetable_timetableentry WHERE part < 0) THEN
        RAISE EXCEPTION 'part نامعتبر است';
    END IF;
END $$;

COMMIT;
