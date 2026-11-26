"""Turn provider webhook payloads into one neutral shape:
   {"id": str, "type": "user_joined"|"user_left"|"meeting_ended", "meeting_id": str, "user_id": str, "ts": datetime}

Two shapes are understood: our generic relay format ({"events": [...]}) and a BigBlueButton-webhooks style payload.
VERIFY the BBB shape against your deployed BBB/webhook version before relying on it."""
import hashlib
import json
from datetime import datetime, timezone

TYPES = {"user_joined", "user_left", "meeting_ended"}
BBB_MAP = {"user-joined": "user_joined", "user-left": "user_left", "meeting-ended": "meeting_ended"}


def _ts(value):
    try:
        v = float(value)
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def normalize(payload):
    out = []
    items = payload.get("events") if isinstance(payload, dict) and "events" in payload else [payload]
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("data"), dict) and item["data"].get("id") in BBB_MAP:        # BBB-webhooks style
            d = item["data"]
            attrs = d.get("attributes", {})
            etype = BBB_MAP[d["id"]]
            meeting = (attrs.get("meeting") or {}).get("external-meeting-id", "")
            user = (attrs.get("user") or {}).get("external-user-id", "")
            ts = _ts((d.get("event") or {}).get("ts"))
        else:                                                                               # generic relay format
            etype, meeting, user, ts = item.get("type"), item.get("meeting_id", ""), item.get("user_id", ""), _ts(item.get("ts"))
        if etype not in TYPES or not meeting or ts is None:
            continue
        eid = str(item.get("id") or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest())[:100]
        out.append({"id": eid, "type": etype, "meeting_id": str(meeting)[:100], "user_id": str(user)[:40], "ts": ts,
                    "raw": item})
    return out
