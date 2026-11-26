import hashlib
import hmac
import json
import time

from django.db import IntegrityError, transaction
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.integrations.models import ExternalSystem

from .models import ClassSession, ProviderEvent
from .normalize import normalize
from .tasks import process_provider_event

MAX_BODY = 64 * 1024
TOLERANCE = 300


class ProviderWebhookView(APIView):
    """POST /api/hooks/<system_id>/ — HMAC-signed, replay-limited, idempotent.

    Headers: X-Timestamp (unix seconds) and X-Signature: sha256=<hex>, where the signature is
    HMAC-SHA256(secret, f"{timestamp}." + raw_body) and the secret is the system's env-var secret."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []        # protect with HMAC + body cap here, and with a rate limit at the load balancer

    def post(self, request, system_id):
        body = request.body
        deny = Response({"detail": "unauthorized"}, status=401)           # same answer for every failure
        if len(body) > MAX_BODY:
            return Response({"detail": "too large"}, status=413)
        system = ExternalSystem.objects.filter(pk=system_id, is_active=True).first()
        secret = system.secret() if system else ""
        ts, sig = request.headers.get("X-Timestamp", ""), request.headers.get("X-Signature", "")
        if not (secret and ts.isdigit() and abs(time.time() - int(ts)) <= TOLERANCE):
            return deny
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig.removeprefix("sha256="), expected):
            return deny
        try:
            events = normalize(json.loads(body))
        except ValueError:
            return Response({"detail": "invalid json"}, status=400)
        accepted = 0
        for ev in events[:200]:
            session = ClassSession.objects.filter(system=system, external_id=ev["meeting_id"]).first()
            try:
                with transaction.atomic():
                    row = ProviderEvent.objects.create(
                        system=system, external_event_id=ev["id"], event_type=ev["type"], session=session,
                        user_ext=ev["user_id"], occurred_at=ev["ts"], payload=ev["raw"])
            except IntegrityError:
                continue                                                  # duplicate delivery: ignore
            accepted += 1
            transaction.on_commit(lambda pk=row.pk: process_provider_event.delay(pk))
        return Response({"accepted": accepted}, status=202)
