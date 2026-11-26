"""Run before anything else:  py tools\\check_install.py
Finds an incomplete or mixed-up copy of the project (for example some patch zips extracted over an old folder)."""
import ast
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
settings = (root / "config" / "settings.py").read_text(encoding="utf-8")
apps = re.findall(r'"(apps\.[a-z_]+)"', settings)
problems = []
for a in sorted(set(apps)):
    folder = root / a.replace(".", "/")
    if not (folder / "__init__.py").is_file():
        problems.append(f"missing app folder: {a}  ({folder})")
for f in root.rglob("*.py"):
    if "venv" in f.parts or "__pycache__" in f.parts:
        continue
    try:
        ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError as e:
        problems.append(f"syntax error: {f.relative_to(root)} line {e.lineno}")
    except UnicodeDecodeError:
        problems.append(f"unreadable (not UTF-8): {f.relative_to(root)}")
# every app folder must be listed in settings (and vice versa) and settings must be the NEW one
folders = {"apps." + d.name for d in (root / "apps").iterdir() if (d / "apps.py").is_file()}
for missing in sorted(folders - set(apps)):
    problems.append(f"config/settings.py is outdated: {missing} is not in INSTALLED_APPS")
for name in ("ADMIN_ENABLED", "PRIVATE_MEDIA_ROOT", "SMS_ENCRYPTION_KEY", "MultiPartParser", "MFA_ENFORCE"):
    if name not in settings:
        problems.append(f"config/settings.py is outdated: '{name}' is missing (use settings.py from the full project zip)")
# the single-page app must still define every page function (an earlier edit once dropped some by accident)
tpl = (root / "apps" / "core" / "templates" / "app.html").read_text(encoding="utf-8")
for fn in ("reportsView", "viewMyEvaluation", "evaluationPolicyCard", "viewTeacherAttendance", "showStudentHistory", "viewCalendar",
           "viewTimetable", "viewImport", "viewCatalogImport", "viewMfaAdmin", "viewBulkZip", "viewManage", "sysTools", "sysList", "sysForm",
           "manageContent", "showContent", "richEditor", "orgCascade", "qrSvg", "buildFrame", "menuGroups", "viewStaffExams",
           "viewBells", "voiceRecorder", "assignmentForm", "viewAssignments", "filePicker", "viewCatalogImport"):
    if f"function {fn}(" not in tpl:
        problems.append(f"apps/core/templates/app.html is incomplete: function {fn} is missing")
web = (root / "apps" / "core" / "web.py").read_text(encoding="utf-8")
for name in ("def app", "def system_panel"):
    if name not in web:
        problems.append(f"apps/core/web.py is outdated: '{name}' is missing")
empty = [str(f.relative_to(root)) for f in root.rglob("*.py")
         if f.stat().st_size == 0 and f.name != "__init__.py" and "venv" not in f.parts]
problems += [f"empty file: {e}" for e in empty]
if problems:
    print("PROBLEMS FOUND:")
    print("\n".join(" - " + p for p in problems))
    print("\nFix: extract the FULL project zip into a new empty folder and use that folder.")
    sys.exit(1)
print(f"OK: {len(set(apps))} apps present, all files readable.")
