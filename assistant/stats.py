"""Turn the anonymised usage log into numbers for /stats and /dashboard."""

import datetime as dt
from collections import Counter, defaultdict


def summarize(events: list[dict], profiles: int = 0) -> dict:
    answers = [e for e in events if e.get("event") == "answer"]
    fb = [e for e in events if e.get("event") == "feedback" and e.get("positive") is not None]
    pos = sum(1 for e in fb if e["positive"])
    lat = sorted(e["latency_s"] for e in answers if "latency_s" in e)

    days_by_user = defaultdict(set)
    for e in answers:
        days_by_user[e["user"]].add(dt.date.fromtimestamp(e["ts"]))
    active = [u for u, d in days_by_user.items() if d]
    returning = sum(1 for u in active if len(days_by_user[u]) >= 2)

    def count(name):
        return sum(1 for e in events if e.get("event") == name)

    by_lang_tokens = defaultdict(list)
    for e in answers:
        if e.get("lang") and "out_tokens" in e:
            by_lang_tokens[e["lang"]].append(e["out_tokens"])

    return {
        "unique_users": len({e["user"] for e in events}),
        "answers": len(answers),
        "by_mode": dict(Counter(e.get("mode") for e in answers if e.get("mode"))),
        "by_language": dict(Counter(e.get("lang") for e in answers if e.get("lang"))),
        "by_task": dict(Counter(e.get("task") or "free" for e in answers).most_common(8)),
        "photos": sum(1 for e in answers if e.get("has_image")),
        "documents": sum(1 for e in answers if e.get("has_document")),
        "voice_answers": sum(1 for e in answers if e.get("voice")),
        "voice_unclear": count("voice_unclear"),
        "voice_unavailable": count("voice_unavailable") + count("voice_note_unsupported"),
        "follow_up_taps": count("button"),
        "ratings": len(fb),
        "positive_ratings": pos,
        "satisfaction_pct": round(100 * pos / len(fb)) if fb else None,
        "returning_users": returning,
        "returning_pct": round(100 * returning / len(active)) if active else None,
        "emergency_cards": count("emergency_card"),
        "returns_drafted": count("return_drafted"),
        "register_pages": count("register_page"),
        "register_refused": count("register_refused"),
        "followups_added": count("followup_added"),
        "followup_visits_done": count("followup_done"),
        "family_reminders": sum(e.get("n", 0) for e in events if e.get("event") == "followup_reminders"),
        "weekly_lists_sent": sum(1 for e in events if e.get("event") == "digest" and e.get("sent")),
        "stock_checks": count("stock_check"),
        "visits_logged": count("visit_logged"),
        "referrals_made": count("referral_made"),
        "referrals_arrived": count("referral_arrived"),
        "referral_completion_pct": round(100 * count("referral_arrived") / count("referral_made")) if count("referral_made") else None,
        "alerts_raised": count("alert_raised"),
        "team_joins": count("team_joined"),
        "notices_sent": count("notice_sent"),
        "stockouts_flagged": sum(e.get("stocked_out", 0) for e in events if e.get("event") == "stock_check"),
        "shares": count("share"),
        "people_remembered": profiles,
        "summaries_folded": count("summary_folded"),
        "errors": count("error"),
        "delivery_failures": count("delivery_failed"),
        "avg_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
        "p90_latency_s": lat[max(0, int(len(lat) * 0.9) - 1)] if lat else None,
        "avg_output_tokens_by_language": {k: round(sum(v) / len(v)) for k, v in by_lang_tokens.items()},
    }
