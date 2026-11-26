from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import role_required
from apps.accounts.models import User

from .perf import snapshot

SuperOnly = role_required(User.Role.SUPER_ADMIN)


class MonitorView(APIView):
    """GET /api/system/monitor/ — CPU, RAM, swap, disk, process, database ping, cache (Redis), Celery queues, request timing."""
    permission_classes = [SuperOnly]

    def get(self, request):
        return Response(snapshot())
