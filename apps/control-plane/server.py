#!/usr/bin/env python3
"""
apps/control-plane/server.py: ClearWright local control plane demo (early alpha).

A small, local-only web control plane that demonstrates the ClearWright clearance
model against a generic sample software project. It is a local reference
implementation and a demonstration surface only. It is human-commanded and
operator-controlled: it does not execute proposed actions, connect to any external
service, run a background worker, or act on its own.

It serves a static page plus a tiny JSON API. Every operator decision is carried
out by the existing ClearWright tools, invoked as subprocesses:

  tools/clearwright_decide.py     grant CTA, deny DTA, or request RFI
  tools/clearwright_claim.py      claim a cleared packet into in-progress
  tools/clearwright_lifecycle.py  complete (DONE) or fail (FAILED)
  tools/clearwright_validate.py   validate packets

Runtime clearance packets are demo data. They are written to a temporary queue
root created at startup (outside the repository) and are never committed. The
queue is seeded from examples/demo_packets/ and can be reset from the UI.

No database, no external runtime dependencies, no network service beyond this
local demo server, no secrets, no autonomous execution.

Run:
  python apps/control-plane/server.py
Then open the printed local address in a browser.
"""
import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
TOOLS = os.path.join(REPO_ROOT, "tools")

# Constants only (enum value sets for the intake form). All packet mutation
# still happens through the tools as subprocesses; the server stays a thin
# driver and never re-implements clearance logic.
sys.path.insert(0, TOOLS)
import clearwright_validate as wpv  # noqa: E402
import clearwright_agent_event as cwae  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_archive as cwarch  # noqa: E402
import clearwright_writer_lock as cwl  # noqa: E402

# The single documented request-body cap for POST bodies: worst-case JSON
# string-escape expansion of a MESSAGE_MAX_BYTES message, plus a fixed
# envelope allowance for the surrounding JSON fields. Independent of, and
# larger than, the message-content limit itself (see clearwright_message).
REQUEST_MAX_BYTES = cwm.MESSAGE_MAX_BYTES * 6 + 8192
STATIC = os.path.join(HERE, "static")
DEMO_PACKETS = os.path.join(REPO_ROOT, "examples", "demo_packets")
MISSION_FILE = os.path.join(REPO_ROOT, "examples", "sample_project", "mission.json")

LANES = [
    "clearance_outbox",
    "clearance_in_progress",
    "clearance_done",
    "clearance_failed",
]

# Command-authority example id for the demo. Personal names are never used.
DEMO_ACTOR = "OPERATOR-0001"

# Actions that require a non-empty reason from the operator.
REASON_ACTIONS = {"dta", "rfi", "fail"}

# Generic target labels the intake form may use. Keeping intake constrained to
# these labels is a confidentiality guard: no private product names, paths, or
# proprietary copy can enter a packet from the UI.
APPROVED_TARGET_LABELS = [
    "sample software project",
    "sample web application",
    "demo target project",
    "local test project",
    "private demo target",
]

# Required fields for RTA intake; the request tool re-validates authoritatively.
REQUEST_REQUIRED = ["title", "packet_type", "requesting_agent", "requested_action"]


# --------------------------------------------------------------------------- #
# Demo queue management (temporary, outside the repository)
# --------------------------------------------------------------------------- #

def make_queue_root():
    """Create a fresh temporary queue root with the four canonical lanes."""
    root = tempfile.mkdtemp(prefix="clearwright_demo_")
    for lane in LANES:
        os.makedirs(os.path.join(root, lane), exist_ok=True)
    return root


def ensure_lanes(root):
    """Create the four canonical lane directories under root if missing.
    Never deletes anything; safe on an existing durable queue."""
    for lane in LANES:
        os.makedirs(os.path.join(root, lane), exist_ok=True)


def queue_has_packets(root):
    """True if any lane already contains a .json packet."""
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if os.path.isdir(lane_dir) and any(
            n.endswith(".json") for n in os.listdir(lane_dir)
        ):
            return True
    return False


def seed_queue(root):
    """Reset the demo queue: clear all lanes, then copy the seed RTA packets
    into clearance_outbox. Only .json files are touched."""
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        os.makedirs(lane_dir, exist_ok=True)
        for name in os.listdir(lane_dir):
            if name.endswith(".json"):
                os.remove(os.path.join(lane_dir, name))
    outbox = os.path.join(root, "clearance_outbox")
    for name in sorted(os.listdir(DEMO_PACKETS)):
        if name.endswith(".json"):
            shutil.copy2(os.path.join(DEMO_PACKETS, name), os.path.join(outbox, name))


OPERATOR_MODE = "operator"
DEMO_MODE = "demo"


def resolve_mode(queue_root, mode):
    """Decide the effective mode. Explicit --mode wins. Otherwise a
    --queue-root defaults to operator mode (live local use) and the default
    temporary queue stays in demo mode (local walkthrough)."""
    if mode in (OPERATOR_MODE, DEMO_MODE):
        return mode
    return OPERATOR_MODE if queue_root else DEMO_MODE


def resolve_queue(queue_root, mode=None):
    """Return (root, durable, mode, demo_seeded).

    Demo mode seeds the demo packets into an empty queue (temporary or durable).
    Operator mode NEVER seeds: a fresh operator queue starts empty and stays a
    real local workspace. An existing queue that already holds packets is left
    exactly as-is in either mode, never cleared or overwritten.
    """
    mode = resolve_mode(queue_root, mode)
    if queue_root:
        root = os.path.abspath(queue_root)
        os.makedirs(root, exist_ok=True)
        ensure_lanes(root)
        durable = True
    else:
        root = make_queue_root()
        durable = False
    demo_seeded = False
    if mode == DEMO_MODE and not queue_has_packets(root):
        seed_queue(root)
        demo_seeded = True
    return root, durable, mode, demo_seeded


def do_reset(root, mode):
    """Reset the queue to the demo seed packets. Allowed only in demo mode, so
    operator mode's live governed work can never be destroyed by a reset."""
    if mode != DEMO_MODE:
        return {"ok": False,
                "error": "reset is only available in demo mode; operator mode "
                         "runs a live durable queue and is never reset"}
    seed_queue(root)
    return {"ok": True}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_json_safe(path):
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


def find_packet(root, filename):
    """Locate a packet by base filename across the lanes. Returns (path, lane)
    or (None, None). basename guards against path traversal."""
    filename = os.path.basename(filename or "")
    if not filename.endswith(".json"):
        return None, None
    for lane in LANES:
        path = os.path.join(root, lane, filename)
        if os.path.isfile(path):
            return path, lane
    return None, None


# --------------------------------------------------------------------------- #
# Allowed operator actions per status and lane (the demo enforcement point)
# --------------------------------------------------------------------------- #

def allowed_actions(status, lane):
    """Which operator actions the demo permits for a packet.

    This is where the demo keeps the clearance model honest:
      - a pre-claim RTA / IN_REVIEW packet may be cleared, denied, or sent to RFI,
      - a cleared CTA packet may only be claimed (it stays in the outbox until
        then),
      - an RFI_PENDING packet is parked as pre-decision clarification only,
      - an IN_PROGRESS packet may be completed or failed,
      - FAILED is therefore never reachable before a claim,
      - terminal packets (DONE, DTA, FAILED, SUPERSEDED) offer no actions.
    """
    if lane == "clearance_outbox":
        if status in ("RTA", "IN_REVIEW"):
            return ["cta", "dta", "rfi"]
        if status == "CTA":
            return ["claim"]
        if status == "RFI_PENDING":
            return []
    if lane == "clearance_in_progress" and status == "IN_PROGRESS":
        return ["complete", "fail"]
    return []


# --------------------------------------------------------------------------- #
# Running the existing ClearWright tools
# --------------------------------------------------------------------------- #

def run_tool(argv):
    """Run a tool as a subprocess. argv is the argument list after the
    interpreter. Returns (returncode, combined_output)."""
    proc = subprocess.run(
        [sys.executable] + argv,
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tool_argv(action, path, reason, results=None):
    """Build the tool argument list for an action on a resolved packet path."""
    decide = os.path.join(TOOLS, "clearwright_decide.py")
    claim = os.path.join(TOOLS, "clearwright_claim.py")
    lifecycle = os.path.join(TOOLS, "clearwright_lifecycle.py")
    if action == "cta":
        return [decide, "cta", path, "--actor", DEMO_ACTOR]
    if action == "dta":
        return [decide, "dta", path, "--actor", DEMO_ACTOR, "--reason", reason]
    if action == "rfi":
        return [decide, "rfi", path, "--actor", DEMO_ACTOR, "--reason", reason]
    if action == "claim":
        return [claim, path, "--claimant", DEMO_ACTOR]
    if action == "complete":
        argv = [lifecycle, "complete", path, "--actor", DEMO_ACTOR]
        results = results if isinstance(results, dict) else {}
        if results.get("summary"):
            argv += ["--summary", str(results["summary"])]
        if results.get("verification"):
            argv += ["--verification", str(results["verification"])]
        for changed in results.get("changed_files") or []:
            if str(changed).strip():
                argv += ["--changed-file", str(changed).strip()]
        if results.get("findings"):
            argv += ["--findings", str(results["findings"])]
        return argv
    if action == "fail":
        return [lifecycle, "fail", path, "--reason", reason, "--actor", DEMO_ACTOR]
    return None


def do_action(root, action, filename, reason="", results=None):
    """Perform one operator action, enforcing the demo's allowed-action policy
    before invoking any tool. Returns a result dict."""
    reason = (reason or "").strip()
    path, lane = find_packet(root, filename)
    if path is None:
        return {"ok": False, "error": "packet not found: {}".format(filename), "output": ""}

    packet = load_json(path)
    status = packet.get("status")
    permitted = allowed_actions(status, lane)
    if action not in permitted:
        return {
            "ok": False,
            "error": "action {!r} is not allowed for status {!r} in {}".format(
                action, status, lane
            ),
            "output": "",
        }
    if action in REASON_ACTIONS and not reason:
        return {"ok": False, "error": "a non-empty reason is required for {}".format(action), "output": ""}

    argv = tool_argv(action, path, reason, results)
    if argv is None:
        return {"ok": False, "error": "unknown action: {}".format(action), "output": ""}

    code, output = run_tool(argv)
    return {"ok": code == 0, "returncode": code, "output": output}


def do_agent_event(root, payload):
    """Record one agent event via the shared adapter. The server is a thin
    driver: it builds and writes through clearwright_agent_event, which is the
    same code path the CLI uses. Returns a result dict."""
    fields = payload if isinstance(payload, dict) else {}
    try:
        event = cwae.build_event(
            fields.get("actor"), fields.get("message"),
            role=fields.get("role") or cwae.DEFAULT_ROLE,
            packet_id=fields.get("packet_id"),
            severity=fields.get("severity") or cwae.DEFAULT_SEVERITY,
            source=fields.get("source") or "local-http",
            simulated=bool(fields.get("simulated", False)),
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwae.write_event(root, event)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "event": event}


def do_message(root, payload, respond=False):
    """Post one local message via the shared adapter. The server is a thin
    driver: it builds and writes through clearwright_message, the same code
    path the CLI uses. On respond, a thread_id is required and the message
    defaults to an outbound response.

    Target integrity: when both thread_id and work_item_id are supplied they
    must already be bound together in the durable record (an existing message
    in that thread carrying that work_item_id); a mismatched pair is refused
    rather than silently creating a cross-target record.

    Idempotency: an optional idempotency_key makes a retried POST safe --
    an exact repeat (same thread, key, target, and canonical content) returns
    the ORIGINAL message id, never a duplicate; a reused key with different
    content or target is refused as a conflict.

    Returns a result dict with an ``error_code`` the HTTP layer maps to the
    matching status (413 too large, 409 idempotency conflict, 400 otherwise)."""
    fields = payload if isinstance(payload, dict) else {}
    thread_id = fields.get("thread_id")
    work_item_id = fields.get("work_item_id")
    if respond and not (thread_id and str(thread_id).strip()):
        return {"ok": False, "error": "respond requires a thread_id"}
    if thread_id and work_item_id:
        bound = any(m.get("work_item_id") == work_item_id
                    for m in cwm.read_messages(root, thread_id=thread_id))
        if not bound:
            return {"ok": False, "error": "thread_id and work_item_id are not "
                    "bound together in the durable record",
                    "error_code": "target_mismatch"}
    direction = fields.get("direction") or ("outbound" if respond else cwm.DEFAULT_DIRECTION)
    status = fields.get("status") or ("responded" if respond else cwm.DEFAULT_STATUS)
    try:
        message = cwm.build_message(
            fields.get("actor"), fields.get("message"),
            role=fields.get("role") or cwm.DEFAULT_ROLE,
            packet_id=fields.get("packet_id"),
            thread_id=thread_id,
            direction=direction,
            status=status,
            source=fields.get("source") or "local-http",
            simulated=bool(fields.get("simulated", False)),
            intent=fields.get("intent"),
            work_item_id=work_item_id,
            idempotency_key=fields.get("idempotency_key"),
        )
    except cwm.MessageTooLarge as exc:
        return {"ok": False, "error": str(exc), "error_code": "message_too_large",
                "limit_bytes": cwm.MESSAGE_MAX_BYTES}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        stored, is_retry = cwm.write_message_idempotent(root, message)
    except cwm.IdempotencyConflict as exc:
        return {"ok": False, "error": "idempotency_key already used with "
                "different content or target", "error_code": "idempotency_conflict",
                "existing_message_id": exc.existing.get("message_id")}
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": stored, "thread_id": stored["thread_id"],
            "message_id": stored["message_id"],
            "work_item_id": stored.get("work_item_id"),
            "canonical_sha256": cwm.canonical_sha256(stored.get("message", "")),
            "idempotent_retry": is_retry}


_MESSAGE_ERROR_STATUS = {
    "message_too_large": 413,
    "idempotency_conflict": 409,
    "maintenance_in_progress": 503,
}


def _message_status_code(result):
    if result.get("ok"):
        return 200
    return _MESSAGE_ERROR_STATUS.get(result.get("error_code"), 400)


def do_request(root, fields):
    """Author one new RTA in the demo queue via the request tool.

    The server only checks field presence and the generic-label constraint;
    the request tool remains the validation authority and performs the
    exclusive write."""
    fields = fields if isinstance(fields, dict) else {}
    missing = [f for f in REQUEST_REQUIRED
               if not str(fields.get(f) or "").strip()]
    if missing:
        return {"ok": False,
                "error": "missing required field(s): {}".format(", ".join(missing)),
                "output": ""}

    label = str(fields.get("target_label") or APPROVED_TARGET_LABELS[0]).strip()
    if label not in APPROVED_TARGET_LABELS:
        return {"ok": False,
                "error": "target label must be one of the approved generic labels",
                "output": ""}

    argv = [
        os.path.join(TOOLS, "clearwright_request.py"), root,
        "--title", str(fields["title"]).strip(),
        "--type", str(fields["packet_type"]).strip(),
        "--agent", str(fields["requesting_agent"]).strip(),
        "--action", str(fields["requested_action"]).strip(),
        "--target-label", label,
    ]
    for key, flag in (("allowed_scope", "--scope"), ("test_command", "--test-command"),
                      ("risk_notes", "--risk")):
        value = str(fields.get(key) or "").strip()
        if value:
            argv += [flag, value]
    for key, flag in (("authority_class", "--authority"),
                      ("clearance_class", "--clearance"),
                      ("priority_class", "--priority")):
        value = str(fields.get(key) or "").strip()
        if value:
            argv += [flag, value]

    code, output = run_tool(argv)
    return {"ok": code == 0, "returncode": code, "output": output}


# --------------------------------------------------------------------------- #
# Agent conversation console (SIMULATED)
#
# The console demonstrates how bounded multi-agent deliberation can be
# condensed into a single human decision. Every turn below is generated
# locally by this demo server: there is NO real external model integration,
# no API call, and no credential anywhere in this repository. Consensus or
# chatter never grants authority; the operator decides.
# --------------------------------------------------------------------------- #

MAX_ROUNDS = 5

# Simulated risk classification for the demo. Deliberately simple keyword
# heuristics: enough to demonstrate that unsafe wording must never condense
# into a CTA recommendation.
DESTRUCTIVE_TERMS = [
    "delete", "drop", "wipe", "erase", "destroy", "remove all", "truncate",
    "purge", "force push", "bypass", "disable auth", "disable security",
    "override safety",
]
CAUTION_TERMS = [
    "auth", "authentication", "credential", "secret", "token", "permission",
    "access control", "security", "migrate", "everything", "all files",
    "not sure", "unclear", "somehow",
]
SAFE_HINT_TERMS = [
    "docs", "readme", "typo", "comment", "label", "version reference",
    "test", "health check", "rename", "documentation", "consistency",
]


def classify_question(question):
    """Return (recommended, risk_level, rationale) for a question.

    Simulated policy: destructive wording is denied (DTA), ambiguous or
    security-adjacent wording needs information (RFI), and clearly bounded
    low-risk improvement wording may be cleared (CTA). Unsafe wording must
    never produce a CTA recommendation.
    """
    q = question.lower()
    for term in DESTRUCTIVE_TERMS:
        if term in q:
            return ("DTA", "high",
                    "destructive wording ({!r}) cannot be cleared".format(term))
    for term in CAUTION_TERMS:
        if term in q:
            return ("RFI", "medium",
                    "wording touches {!r}; scope must be clarified first".format(term))
    for term in SAFE_HINT_TERMS:
        if term in q:
            return ("CTA", "low",
                    "bounded, low-risk improvement wording ({!r})".format(term))
    return ("RFI", "medium", "scope is not explicit enough to clear directly")


def build_conversation(question):
    """Build one bounded, simulated multi-agent deliberation (max 5 turns)
    plus a condensed ClearWright decision summary. Pure function: no packet
    is written and nothing external is called."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "a non-empty question is required"}

    recommended, risk_level, rationale = classify_question(question)
    short = question if len(question) <= 120 else question[:117] + "..."

    turns = [
        {
            "role": "claude", "kind": "analysis",
            "text": "Analysis: the operator is asking: {!r}. Framed against the "
                    "mission scope, this is a {} risk request; the deciding factor "
                    "is {}.".format(short, risk_level, rationale),
        },
        {
            "role": "gpt", "kind": "challenge",
            "text": "Challenge: before any clearance, confirm the blast radius. "
                    "Risk read: {}. {} Watch for scope creep beyond the sample "
                    "project boundary.".format(
                        risk_level,
                        "This should not be cleared as asked." if recommended != "CTA"
                        else "Bounded as stated, no blocking objection."),
        },
        {
            "role": "codex", "kind": "code_impact",
            "text": "Code/test impact: {} A regression check should accompany any "
                    "change.".format(
                        "no code path should be touched until scope is settled."
                        if recommended != "CTA" else
                        "the change is small and testable."),
            "code_impact": (
                "def test_change_is_bounded():\n"
                "    # demo test idea (simulated): assert the change touches only\n"
                "    # the approved files and the suite still passes\n"
                "    ..."),
        },
        {
            "role": "claude", "kind": "revised_recommendation",
            "text": "Revised recommendation: {} — {}.".format(recommended, rationale),
        },
        {
            "role": "gpt", "kind": "final_review",
            "text": "Final review: concur with {} at {} risk. The operator holds "
                    "the decision; this deliberation only informs it.".format(
                        recommended, risk_level),
        },
    ][:MAX_ROUNDS]

    if recommended == "CTA":
        proposed_action = ("Draft an RTA for the bounded improvement and submit it "
                           "for operator decision.")
        rta_title = "Bounded improvement: {}".format(short)
        rta_action = ("{} Documentation/config-level change only. No functional "
                      "behavior changes without a separate clearance.".format(question))
        scope_boundary = "only the files named in the RTA; verify before DONE"
    elif recommended == "DTA":
        proposed_action = ("Deny as asked. If a safe subset exists, restate it as a "
                           "new, narrower question.")
        rta_title = None
        rta_action = None
        scope_boundary = "no action is in scope as worded"
    else:
        proposed_action = ("Request information: restate the question with explicit "
                           "scope, affected files, and rollback expectations.")
        rta_title = None
        rta_action = None
        scope_boundary = "undetermined until the RFI is answered"

    summary = {
        "role": "clearwright",
        "decision_needed": "Operator decision on: {}".format(short),
        "recommended": recommended,
        "risk_level": risk_level,
        "risks": [rationale,
                  "consensus or agent agreement does not grant authority",
                  "any work requires a granted, bounded clearance"],
        "scope_boundary": scope_boundary,
        "proposed_next_action": proposed_action,
        "proposed_rta_title": rta_title,
        "proposed_rta_action": rta_action,
    }

    return {
        "ok": True,
        "simulated": True,
        "max_rounds": MAX_ROUNDS,
        "question": question,
        "turns": turns,
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# Board / audit state
# --------------------------------------------------------------------------- #

def audit_events(packet):
    audit = packet.get("audit_json")
    if isinstance(audit, dict) and isinstance(audit.get("events"), list):
        return audit["events"]
    return []


def packet_summary(path, lane):
    packet = load_json(path)
    inputs = packet.get("inputs_json") if isinstance(packet.get("inputs_json"), dict) else {}
    status = packet.get("status")
    events = audit_events(packet)
    return {
        "last_event_at": events[-1].get("at") if events else None,
        "filename": os.path.basename(path),
        "lane": lane,
        "packet_id": packet.get("packet_id"),
        "action": packet.get("title"),
        "role": packet.get("requesting_agent"),
        "status": status,
        "authority": packet.get("authority_class"),
        "clearance_class": packet.get("clearance_class"),
        "risk_notes": packet.get("risk_notes"),
        "clearance_expires_at": packet.get("clearance_expires_at"),
        "requested_action": inputs.get("requested_action"),
        "allowed_scope": inputs.get("allowed_scope"),
        "audit_event_count": len(audit_events(packet)),
        "allowed_actions": allowed_actions(status, lane),
    }


PULSE_RECENCY_SECONDS = 300  # 5 minutes: activity/pulse recency window.
TERMINAL_RECENT_SECONDS = 24 * 3600  # 24 hours: "recent terminal packet" display window.


def _within(iso, now, seconds):
    """True if the ISO timestamp is no older than `seconds` before `now`. A
    just-written (or slightly future, from monotonic-clock skew) timestamp still
    counts as recent. Robust to microsecond and non-microsecond ISO, with or
    without a trailing Z."""
    if not iso:
        return False
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() <= seconds


def compute_pulse(root, now=None):
    """Which workflow stages should pulse, from real durable state only.

    DONE pulses only for a recent completion (a recent responded/outbound
    message, or a clearance_done packet whose latest audit event is recent),
    never merely because clearance_done still holds an old packet. Simulated
    messages never drive the pulse.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    items = cww.derive_work_items(root)
    messages = [m for m in cwm.read_messages(root) if not m.get("simulated")]

    outbox_status, done_packets, inprog_count = [], [], 0
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            try:
                pkt = load_json(os.path.join(lane_dir, name))
            except (OSError, ValueError):
                continue
            if lane == "clearance_outbox":
                outbox_status.append(pkt.get("status"))
            elif lane == "clearance_in_progress":
                inprog_count += 1
            elif lane == "clearance_done":
                done_packets.append(pkt)

    open_items = [i for i in items if i.get("status") == "open"]
    claimed_items = [i for i in items if i.get("status") == "claimed"]

    def is_reviewer(m):
        return m.get("actor") == "codex" or m.get("role") == "reviewer"

    recent = [m for m in messages if _within(m.get("at"), now, PULSE_RECENCY_SECONDS)]
    recent_progress = any(m.get("direction") == "internal" for m in recent)
    recent_reviewer = any(is_reviewer(m) for m in recent)
    recent_response = any(m.get("direction") == "outbound" or m.get("status") == "responded"
                          for m in recent)
    recent_done_packet = None  # (packet_id, last event at) of a recent completion
    for pkt in done_packets:
        evs = audit_events(pkt)
        if evs and _within(evs[-1].get("at"), now, PULSE_RECENCY_SECONDS):
            recent_done_packet = (pkt.get("packet_id"), evs[-1].get("at"))
            break

    pulse = {
        "incoming": bool(open_items) or any(s in ("RTA", "IN_REVIEW", "RFI_PENDING", "CTA") for s in outbox_status),
        "decision": any(s in ("RTA", "IN_REVIEW") for s in outbox_status),
        "cta": any(s == "CTA" for s in outbox_status),
        "rfi": any(s == "RFI_PENDING" for s in outbox_status),
        "claimed": bool(claimed_items) or inprog_count > 0 or recent_progress,
        "verify": recent_progress or recent_reviewer,
        "done": recent_response or recent_done_packet is not None,
    }

    # Inspector metadata: why the graph is pulsing and when the time-based part
    # stops. The most recent qualifying message wins; standing states (open or
    # claimed work items, packet lifecycle) are the fallback and carry no time
    # expiry - they hold until acted on.
    reason, active_phase = "no recent activity", "idle"
    src_thread = src_item = src_packet = None
    expires_at = seconds_remaining = None

    def expiry_from(at):
        try:
            t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None, None
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        exp = t + timedelta(seconds=PULSE_RECENCY_SECONDS)
        return (exp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                max(0, int((exp - now).total_seconds())))

    candidates = []
    for m in recent:
        if m.get("direction") == "outbound" or m.get("status") == "responded":
            candidates.append((m.get("at", ""), "done", "recent final response", m))
        elif is_reviewer(m):
            candidates.append((m.get("at", ""), "verification", "recent reviewer message", m))
        elif m.get("status") == "claimed":
            candidates.append((m.get("at", ""), "claimed work", "work item claimed", m))
        elif m.get("direction") == "internal":
            candidates.append((m.get("at", ""), "verification", "recent internal progress message", m))
    if candidates:
        at, active_phase, reason, m = max(candidates, key=lambda c: c[0])
        src_thread = m.get("thread_id")
        src_item = m.get("work_item_id")
        src_packet = m.get("packet_id")
        expires_at, seconds_remaining = expiry_from(at)
    elif claimed_items:
        it = claimed_items[0]
        active_phase, reason = "claimed work", "claimed work item in progress (holds until acted on)"
        src_thread, src_item, src_packet = it.get("thread_id"), it.get("work_item_id"), it.get("packet_id")
    elif open_items:
        it = open_items[0]
        kind_phase = {
            "packet": ("cleared to act", "CTA packet ready to claim (holds until claimed)"),
            "rfi": ("rfi", "packet awaiting clarification (RFI_PENDING)"),
            "in_progress": ("claimed work", "packet IN_PROGRESS (holds until completed)"),
        }
        active_phase, reason = kind_phase.get(
            it.get("kind"),
            ("incoming request", "open work item waiting for a worker (holds until acted on)"))
        src_thread, src_item, src_packet = it.get("thread_id"), it.get("work_item_id"), it.get("packet_id")
    elif "RFI_PENDING" in outbox_status:
        active_phase, reason = "rfi", "packet awaiting clarification (RFI_PENDING)"
    elif "CTA" in outbox_status:
        active_phase, reason = "cleared to act", "CTA packet ready to claim"
    elif inprog_count:
        active_phase, reason = "claimed work", "packet IN_PROGRESS"
    elif any(s in ("RTA", "IN_REVIEW") for s in outbox_status):
        active_phase, reason = "operator decision", "packet awaiting operator decision"
    elif recent_done_packet is not None:
        active_phase, reason = "done", "recent packet completion"
        src_packet = recent_done_packet[0]
        expires_at, seconds_remaining = expiry_from(recent_done_packet[1])

    pulse.update({
        "active_phase": active_phase,
        "reason": reason,
        "source_thread_id": src_thread,
        "source_work_item_id": src_item,
        "source_packet_id": src_packet,
        "expires_at": expires_at,
        "seconds_remaining": seconds_remaining,
    })
    return pulse


def wi_status_code(result):
    """HTTP status for a work-item result: 200 ok, 404 when the work item is
    unknown, 400 otherwise. The shared functions never write on an unknown id."""
    if result.get("ok"):
        return 200
    return 404 if result.get("error") == "work_item_not_found" else 400


def build_state(root, mode=None, durable=None, demo_seeded=None):
    """Return the full board: mode metadata, mission, and packets grouped by
    lane. Mode/durable/demo_seeded fall back to the running server's globals."""
    mode = mode if mode is not None else MODE
    durable = durable if durable is not None else DURABLE
    demo_seeded = demo_seeded if demo_seeded is not None else DEMO_SEEDED
    now = datetime.now(timezone.utc)
    lanes = {lane: [] for lane in LANES}
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            try:
                summary = packet_summary(os.path.join(lane_dir, name), lane)
            except (OSError, ValueError):
                summary = {"filename": name, "lane": lane, "status": "UNREADABLE"}
            # UI hint only, files are never touched: a terminal packet in
            # clearance_done older than the 24h recent-terminal window is
            # "archived" - durable audit history, not current work. Failed
            # packets are never archived.
            if (lane == "clearance_done"
                    and summary.get("status") in ("DONE", "DTA", "SUPERSEDED")):
                summary["archived"] = not _within(
                    summary.get("last_event_at"), now, TERMINAL_RECENT_SECONDS)
            lanes[lane].append(summary)
    return {
        "mode": mode,
        "durable": bool(durable),
        "demo_seeded": bool(demo_seeded),
        "queue_root": root,
        "mission": read_mission() if mode == DEMO_MODE else {},
        "lanes": lanes,
        "pulse": compute_pulse(root),
        "actor": DEMO_ACTOR,
        "intake": {
            "target_labels": APPROVED_TARGET_LABELS,
            "packet_types": ["analysis", "code_change", "docs_change",
                             "config_change", "data_change"],
            "authority_classes": sorted(wpv.ALLOWED_AUTHORITY_CLASS),
            "clearance_classes": sorted(wpv.ALLOWED_CLEARANCE_CLASS),
            "priority_classes": sorted(wpv.ALLOWED_PRIORITY_CLASS),
        },
    }


def build_audit(root, filename):
    path, lane = find_packet(root, filename)
    if path is not None:
        packet = load_json(path)
        return {
            "filename": os.path.basename(path),
            "found": True,
            "lane": lane,
            "packet_id": packet.get("packet_id"),
            "status": packet.get("status"),
            "events": audit_events(packet),
            "archived": False,
        }
    # Archive-aware fallback: active always wins; only consulted on a miss.
    archived_path, packet = cwarch.read_archived_clearance_packet(root, filename)
    if archived_path is not None:
        return {
            "filename": os.path.basename(archived_path),
            "found": True,
            "lane": "clearance_done",
            "packet_id": packet.get("packet_id"),
            "status": packet.get("status"),
            "events": audit_events(packet),
            "archived": True,
        }
    return {"filename": filename, "found": False, "events": []}


def read_mission():
    try:
        return load_json(MISSION_FILE)
    except (OSError, ValueError):
        return {}


def build_history(root, packet_id=None, thread_id=None, actor=None,
                  lane=None, status=None):
    """Read-only history across the three durable sources: packets (summaries
    across lanes), messages, and agent events. Optional filters narrow each
    source; nothing here mutates state."""
    packets = []
    for a_lane in LANES:
        if lane and a_lane != lane:
            continue
        lane_dir = os.path.join(root, a_lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            try:
                summary = packet_summary(os.path.join(lane_dir, name), a_lane)
            except (OSError, ValueError):
                summary = {"filename": name, "lane": a_lane, "status": "UNREADABLE"}
            if packet_id and summary.get("packet_id") != packet_id:
                continue
            if status and summary.get("status") != status:
                continue
            packets.append(summary)

    messages = cwm.read_messages(root, packet_id=packet_id, thread_id=thread_id)
    if actor:
        messages = [m for m in messages if m.get("actor") == actor]
    events = cwae.read_events(root, packet_id=packet_id)
    if actor:
        events = [e for e in events if e.get("actor") == actor]
    return {"packets": packets, "messages": messages, "events": events}


def build_ledger(root, scope="active"):
    """Read-only: ONE unified History ledger across every durable source --
    packets, messages, and agent events -- as uniform rows
    {at, type, work_item_id, thread_id, packet_id, council_id, actor, event,
     status, archived, record}. scope: active (default) | archived | all.
    Archived rows are read directly from their archive paths via the archive
    index and labeled archived: true. The client filters further (type, actor,
    status, date, ids, text); nothing here mutates state."""
    rows = []

    def message_row(m, archived):
        body = m.get("message") or ""
        council = None
        cm = re.search(r"\bcw-council-\d{8}T\d+\b", body)
        if cm:
            council = cm.group(0)
        rows.append({
            "at": m.get("at"), "type": "message",
            "work_item_id": m.get("work_item_id"),
            "thread_id": m.get("thread_id"), "packet_id": m.get("packet_id"),
            "council_id": council, "actor": m.get("actor"),
            "event": body[:160], "status": m.get("status"),
            "archived": archived, "record": m,
        })

    def packet_row(summary, archived):
        rows.append({
            "at": summary.get("last_event_at"),
            "type": "packet", "work_item_id": None, "thread_id": None,
            "packet_id": summary.get("packet_id"), "council_id": None,
            "actor": summary.get("role"),
            "event": (summary.get("action") or summary.get("filename") or "")[:160],
            "status": summary.get("status"), "archived": archived,
            "record": summary,
        })

    def event_row(e, archived):
        rows.append({
            "at": e.get("at"), "type": "agent_event", "work_item_id": None,
            "thread_id": None, "packet_id": e.get("packet_id"),
            "council_id": None, "actor": e.get("actor"),
            "event": (e.get("event") or "")[:160] +
                     ((" - " + e.get("note", ""))[:80] if e.get("note") else ""),
            "status": None, "archived": archived, "record": e,
        })

    if scope in ("active", "all"):
        for m in cwm.read_messages(root):
            if not m.get("simulated"):
                message_row(m, False)
        for lane in LANES:
            lane_dir = os.path.join(root, lane)
            if not os.path.isdir(lane_dir):
                continue
            for name in sorted(os.listdir(lane_dir)):
                if not name.endswith(".json"):
                    continue
                try:
                    packet_row(packet_summary(os.path.join(lane_dir, name), lane), False)
                except (OSError, ValueError):
                    continue
        for e in cwae.read_events(root):
            event_row(e, False)

    if scope in ("archived", "all"):
        idx = cwarch._read_json(cwarch.index_path(cwarch.archive_root(root))) or {}
        for rid, entry in (idx.get("ids") or {}).items():
            rtype = entry.get("type")
            for path in entry.get("paths", []):
                data = cwarch._read_json(path)
                if data is None:
                    continue
                if rtype == "thread" and os.sep + "communications" + os.sep in path:
                    message_row(data, True)
                elif rtype == "clearance_packet":
                    try:
                        packet_row(packet_summary(path, "clearance_done"), True)
                    except (OSError, ValueError):
                        continue
                elif rtype == "agent_event":
                    event_row(data, True)

    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return {"rows": rows, "scope": scope, "count": len(rows)}


def parse_codex_telemetry(text):
    """Parse a Codex review footer ("Telemetry: exit=..., elapsed=...s,
    bytes=..., lines=..., timed_out=..., classification=...") into structured
    fields, or None if there is no telemetry footer. Pure and unit-testable."""
    if not text or "Telemetry:" not in text:
        return None

    def grab(key):
        m = re.search(key + r"=([^,\s]+)", text)
        return m.group(1) if m else None

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    elapsed = grab("elapsed")
    timed_out = grab("timed_out")
    classification = grab("classification")
    return {
        "exit_code": as_int(grab("exit")),
        "elapsed_seconds": (float(elapsed.rstrip("s")) if elapsed else None),
        "bytes": as_int(grab("bytes")),
        "lines": as_int(grab("lines")),
        "timed_out": (timed_out.lower() == "true" if timed_out else None),
        "classification": (classification.rstrip(".") if classification else None),
    }


def _real_threads(root):
    """Real (non-simulated) messages grouped by thread_id, preserving order."""
    threads = {}
    for m in cwm.read_messages(root):
        if m.get("simulated"):
            continue
        threads.setdefault(m.get("thread_id"), []).append(m)
    return threads


def build_active_run(root, thread_id=None):
    """One message thread, grouped and ready to render for the Active Run view:
    thread_id, work_item_id, packet_id, the ordered messages, and any parsed
    Codex telemetry. Without thread_id the most recently active actionable or
    worked thread is returned (plain chat is not auto-selected); with thread_id
    any specific run is returned, chat included. Read-only; simulated messages
    are excluded so operator mode stays real-only."""
    threads = _real_threads(root)
    empty = {"thread_id": None, "work_item_id": None, "packet_id": None,
             "messages": [], "codex_telemetry": None}
    if thread_id is not None:
        if thread_id not in threads:
            return empty
        active_tid = thread_id
    else:
        if not threads:
            return empty

        # Plain chat is not auto-selected: the default pick is the most
        # recently active thread with actionable or worked content (a work
        # item, a packet, a claim, a response, or an actionable inbound
        # request). A chat-only thread is selectable explicitly, and is the
        # fallback when nothing actionable exists yet.
        def _worked(tid):
            return any(
                m.get("work_item_id") or m.get("packet_id")
                or m.get("direction") == "outbound"
                or m.get("status") in ("claimed", "responded")
                or (m.get("direction") == "inbound" and m.get("intent") != "chat")
                for m in threads[tid])

        candidates = [t for t in threads if _worked(t)] or list(threads)
        active_tid = max(candidates, key=lambda t: max((mm.get("at", "") for mm in threads[t]), default=""))
    msgs = threads[active_tid]
    work_item_id = next((m.get("work_item_id") for m in msgs if m.get("work_item_id")), None)
    packet_id = next((m.get("packet_id") for m in msgs if m.get("packet_id")), None)
    codex = next((m for m in msgs if m.get("actor") == "codex"), None)
    telemetry = parse_codex_telemetry(codex["message"]) if codex else None
    return {"thread_id": active_tid, "work_item_id": work_item_id,
            "packet_id": packet_id, "messages": msgs, "codex_telemetry": telemetry}


def _run_status(msgs):
    """Derive a run's status from its messages: responded when a final
    outbound/responded message exists, claimed when a claim was recorded,
    chat when every inbound message is plain conversation (intent "chat",
    nothing awaiting action), otherwise open."""
    if any(m.get("direction") == "outbound" or m.get("status") == "responded" for m in msgs):
        return "responded"
    if any(m.get("status") == "claimed" for m in msgs):
        return "claimed"
    inbound = [m for m in msgs if m.get("direction") == "inbound"]
    if inbound and all(m.get("intent") == "chat" for m in inbound):
        return "chat"
    return "open"


# The six operator-facing phases in order. Derived ONLY from the selected
# task's own durable state (its councils, gate, claim, and messages) -- a
# historical or concurrent task can never affect another task's phase display.
TASK_PHASES = ("request", "plan_review", "authority", "execute", "verify", "complete")


def build_task_state(root, thread_id=None, work_item_id=None):
    """Read-only: the selected-task header + phase-stepper model, bound to a
    CANONICAL WORK ITEM. Pass work_item_id to select precisely (primary); a
    thread_id is accepted for compatibility and resolved to its single work
    item, erroring work_item_ambiguous when the thread holds several. Every
    field (title, council, phase, gate, claim, next action, overview) derives
    from the selected work item ALONE, so an authority or chat message can
    never become the title and one task's activity can never relabel another.
    Returns {found, work_item_id, thread_id, title, status, phase, ...}."""
    import clearwright_gate as cwg
    import clearwright_identity as cwid

    # Resolve the selection to a canonical work-item id.
    wid = work_item_id
    if not wid and thread_id:
        thread_items = [it for it in cww.derive_work_items(root, include="all")
                        if it.get("kind") == "message"
                        and it.get("thread_id") == thread_id]
        if len(thread_items) > 1:
            return {"thread_id": thread_id, "found": False,
                    "error": "work_item_ambiguous",
                    "work_item_ids": [it["work_item_id"] for it in thread_items]}
        if thread_items:
            wid = thread_items[0]["work_item_id"]
    if not wid:
        return {"thread_id": thread_id, "found": False}

    item = cww.find_work_item(root, wid)
    mid = cwid.message_id_of(wid)
    origin = None
    if mid:
        origin = next((m for m in cwm.read_messages(root)
                       if m.get("message_id") == mid), None)
    if item is None and origin is None:
        return {"thread_id": thread_id, "found": False, "work_item_id": wid}

    tid = (item or {}).get("thread_id") or (origin or {}).get("thread_id")
    # Bound records for this work item only.
    all_msgs = cwm.read_messages(root)
    bound = [m for m in all_msgs if m.get("work_item_id") == wid]
    # Legacy single-item thread: unbound thread records bind to the sole item.
    if tid:
        thread_item_count = sum(
            1 for it in cww.derive_work_items(root, include="all")
            if it.get("kind") == "message" and it.get("thread_id") == tid)
        if thread_item_count <= 1:
            bound += [m for m in all_msgs if not m.get("work_item_id")
                      and m.get("thread_id") == tid]
    status = (item or {}).get("status") or "open"

    # Title is ALWAYS the work item's ORIGIN message, never a thread scan.
    title_source = origin or item or {}
    title_text = title_source.get("message") or title_source.get("title") or ""

    # Councils bound to THIS work item (thread fallback only for legacy
    # single-item threads).
    councils = [c for c in cwrc.list_councils(root) if c.get("work_item_id") == wid]
    if not councils and tid and thread_item_count <= 1:
        councils = cwrc.list_councils(root, thread_id=tid)
    plan_c = next((c for c in councils if c.get("phase") == "plan"), None)
    verify_c = next((c for c in councils if c.get("phase") == "verify"), None)
    current_council = councils[0] if councils else None

    gate = None
    try:
        gate = cwg.active_gate(root, wid)
    except Exception:
        gate = None

    claim_msg = next((m for m in bound if m.get("status") == "claimed"), None)
    claim = {"claimed": claim_msg is not None,
             "claimed_by": (claim_msg or {}).get("actor"),
             "claimed_at": (claim_msg or {}).get("at")}
    msgs = bound

    plan_agreed = bool(plan_c and plan_c.get("outcome") == "agreement_threshold_met")
    verify_agreed = bool(verify_c and verify_c.get("outcome") == "agreement_threshold_met")
    done = status in ("done", "closed", "superseded") or \
        any(m.get("closure") in ("done", "closed_by_operator") for m in msgs)

    # Phase derivation, in precedence order. Authority (an unresolved gate)
    # renders amber and static; it interrupts whatever phase was underway.
    phase_attention = False
    if done:
        phase = "complete"
    elif gate is not None:
        phase = "authority"
        phase_attention = True
    elif verify_c and not verify_agreed:
        phase = "verify"
    elif plan_agreed and not verify_c:
        phase = "execute"
    elif plan_c and not plan_agreed:
        phase = "plan_review"
    else:
        phase = "request"

    if phase == "complete":
        next_action = "None; the task is terminal"
    elif phase == "authority":
        next_action = ("Operator decision required: resolve gate {} "
                       "(grant-proceed or close)").format((gate or {}).get("gate_id"))
    elif phase == "verify":
        next_action = "Run/rerun verification rounds to agreement, then complete"
    elif phase == "execute":
        next_action = "Execute inside the approved scope, then run verification"
    elif phase == "plan_review":
        next_action = "Continue plan council rounds and reconciliation to agreement"
    elif status == "claimed":
        next_action = "Draft the plan packet and start the plan council"
    else:
        next_action = "Claim the work item to begin"

    # Overview data: the operator-approved scope and verification requirement
    # from the persisted envelope, and the latest reconciliation / blockers
    # from the most recent council's durable record.
    envelope = {}
    if wid and ":" in wid:
        mid = wid.split(":", 1)[1]
        env_path = os.path.join(root, "task_envelopes", mid + ".json")
        envelope = load_json_safe(env_path) or {}
    latest_recon = None
    latest_blockers = []
    if current_council:
        full = cwrc.get_council(root, current_council["council_id"]) or {}
        rounds = [r for r in full.get("rounds", []) if r.get("substantive", True)]
        for r in reversed(rounds):
            if r.get("reconciliation"):
                latest_recon = {
                    "round": r.get("round"),
                    "summary": (r["reconciliation"].get("summary") or "")[:400],
                    "revised_plan": (r["reconciliation"].get("revised_plan") or [])[:6],
                    "ready_to_proceed": r["reconciliation"].get("ready_to_proceed"),
                }
                break
        latest_blockers = ((full.get("outcome") or {}).get("unresolved_blockers")
                           or [])[:6]

    # Audit data: every gate (append-only history) and its authority records,
    # plus the artifact ids/hashes any bound council pinned.
    all_gates = []
    if wid:
        try:
            all_gates = [{"gate_id": g.get("gate_id"),
                          "council_id": g.get("council_id"),
                          "phase": g.get("phase"), "outcome": g.get("outcome"),
                          "created_at": g.get("created_at"),
                          "disposition": g.get("disposition"),
                          "authority": g.get("authority")}
                         for g in cwg.load_gates(root, wid)]
        except Exception:
            all_gates = []
    artifacts = []
    seen_artifacts = set()
    try:
        import clearwright_artifacts as cwa_mod
        for c in councils:
            full = cwrc.get_council(root, c["council_id"]) or {}
            for aid in ((full.get("council") or {}).get("artifact_ids") or []):
                if aid in seen_artifacts:
                    continue
                seen_artifacts.add(aid)
                try:
                    rec = cwa_mod.get(root, aid)
                    artifacts.append({"artifact_id": aid,
                                      "sha256": rec.get("sha256"),
                                      "original_path": rec.get("original_path")})
                except Exception:
                    artifacts.append({"artifact_id": aid})
    except Exception:
        pass

    verification_required = bool(envelope.get("_audit", {}).get(
        "verification_required", envelope.get("verification_required", False)))
    completion_criteria = []
    if verification_required:
        completion_criteria.append("Verification council must reach agreement")
    if gate is not None:
        completion_criteria.append("Unresolved gate must be resolved by operator "
                                   "authority or the item operator-closed")
    if envelope.get("task_kind") in ("governed", "high_risk"):
        completion_criteria.append("Clearance packet must be in clearance_done")
    completion_criteria.append("Completion is recorded via complete "
                               "(DONE) or operator-only close")

    return {
        "thread_id": tid, "found": True,
        "work_item_id": wid, "packet_id": (item or {}).get("packet_id"),
        "title": (title_text or "")[:160],
        "status": status,
        "phase": phase, "phase_attention": phase_attention,
        "phases": list(TASK_PHASES),
        "councils": councils,
        "current_council": current_council and {
            "council_id": current_council.get("council_id"),
            "phase": current_council.get("phase"),
            "outcome": current_council.get("outcome"),
            "rounds": current_council.get("current_round")},
        "gate": gate and {"gate_id": gate.get("gate_id"),
                          "council_id": gate.get("council_id"),
                          "created_at": gate.get("created_at"),
                          "disposition": gate.get("disposition")},
        "gates": all_gates,
        "claim": claim,
        "next_action": next_action,
        "overview": {
            "approved_scope": envelope.get("approved_scope"),
            "request": envelope.get("request"),
            "verification_required": verification_required,
            "latest_reconciliation": latest_recon,
            "blockers": latest_blockers,
            "completion_criteria": completion_criteria,
        },
        "artifacts": artifacts,
    }


def build_archive_index_summary(root):
    """Read-only: the archived record ids (from the archive index) for the
    queue's Archived group and History's archived scope."""
    idx = cwarch._read_json(cwarch.index_path(cwarch.archive_root(root))) or {}
    rows = [{"id": rid, "type": entry.get("type"),
             "paths": len(entry.get("paths", []))}
            for rid, entry in (idx.get("ids") or {}).items()]
    rows.sort(key=lambda r: r["id"])
    return {"archived": rows, "count": len(rows)}


def build_runs(root, limit=None, status=None, actor=None, source=None,
               has_codex=None, packet_id=None):
    """Run registry: summaries of every real message thread, newest-last-
    activity first, derived from the durable message log (no new store).
    Simple filters only; History remains the full ledger."""
    runs = []
    for tid, msgs in _real_threads(root).items():
        first = msgs[0]
        inbound = next((m for m in msgs if m.get("direction") == "inbound"), first)
        codex = next((m for m in msgs if m.get("actor") == "codex"), None)
        run = {
            "thread_id": tid,
            "work_item_id": next((m.get("work_item_id") for m in msgs if m.get("work_item_id")), None),
            "packet_id": next((m.get("packet_id") for m in msgs if m.get("packet_id")), None),
            "title": (inbound.get("message") or "")[:140],
            "first_timestamp": first.get("at"),
            "last_timestamp": msgs[-1].get("at"),
            "message_count": len(msgs),
            "actors": sorted({m.get("actor") for m in msgs if m.get("actor")}),
            "sources": sorted({m.get("source") for m in msgs if m.get("source")}),
            "status": _run_status(msgs),
            "has_codex_review": codex is not None,
            "codex_telemetry": parse_codex_telemetry(codex["message"]) if codex else None,
            "latest_message_preview": (msgs[-1].get("message") or "")[:140],
        }
        runs.append(run)
    runs.sort(key=lambda r: r.get("last_timestamp") or "", reverse=True)

    if status:
        runs = [r for r in runs if r["status"] == status]
    if actor:
        runs = [r for r in runs if actor in r["actors"]]
    if source:
        runs = [r for r in runs if source in r["sources"]]
    if has_codex is not None:
        runs = [r for r in runs if r["has_codex_review"] == has_codex]
    if packet_id:
        runs = [r for r in runs if r["packet_id"] == packet_id]
    if limit is not None and limit >= 0:
        runs = runs[:limit]
    return runs


def _codex_cli_on_path():
    """Cheap capability probe only: is a `codex` executable on PATH? Never
    invokes Codex and never proves participation."""
    try:
        return shutil.which("codex") is not None
    except Exception:  # noqa: BLE001 - a probe failure is "unknown", not an error
        return None


def _openai_key_present():
    """Cheap capability probe only: is OPENAI_API_KEY present in the process
    environment? Returns a boolean and NEVER the value."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def build_health(root, mode=None, durable=None, codex_check=_codex_cli_on_path,
                 key_check=_openai_key_present):
    """Read-only health/readiness snapshot: is ClearWright ready to use right
    now? Derives everything from existing durable state and cheap filesystem
    checks. Never mutates the queue, never runs Codex or tests. Status is
    readiness guidance, not compliance: red = problem, yellow = attention,
    green = ready."""
    mode = mode if mode is not None else MODE
    durable = durable if durable is not None else DURABLE
    warnings, errors = [], []
    health = {
        "ok": False,
        "status": "red",
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "mode": mode,
        "durable": bool(durable),
        "queue_root": root,
        "warnings": warnings,
        "errors": errors,
    }
    try:
        health["queue_root_exists"] = bool(root) and os.path.isdir(root)
        if not health["queue_root_exists"]:
            errors.append("Queue root is missing or unreadable.")

        packet_counts = {}
        for lane in LANES:
            lane_dir = os.path.join(root, lane) if root else ""
            if not os.path.isdir(lane_dir):
                packet_counts[lane] = 0
                if health["queue_root_exists"]:
                    errors.append("Queue lane {} is missing.".format(lane))
                continue
            packet_counts[lane] = len([n for n in os.listdir(lane_dir) if n.endswith(".json")])
        health["packet_counts"] = packet_counts
        if packet_counts.get("clearance_failed", 0) > 0:
            errors.append("{} failed packet(s) in clearance_failed.".format(
                packet_counts["clearance_failed"]))

        health["message_count"] = len(cwm.read_messages(root)) if health["queue_root_exists"] else 0
        health["agent_event_count"] = len(cwae.read_events(root)) if health["queue_root_exists"] else 0

        items = cww.derive_work_items(root) if health["queue_root_exists"] else []
        health["work_items_total"] = len(items)
        health["work_items_open"] = len([i for i in items if i.get("status") == "open"])
        health["work_items_claimed"] = len([i for i in items if i.get("status") == "claimed"])
        if health["work_items_open"]:
            warnings.append("{} open work item(s) waiting for a worker.".format(
                health["work_items_open"]))
        if health["work_items_claimed"]:
            warnings.append("{} claimed work item(s) in progress.".format(
                health["work_items_claimed"]))

        runs = build_runs(root) if health["queue_root_exists"] else []
        health["run_count"] = len(runs)
        health["latest_run_timestamp"] = runs[0]["last_timestamp"] if runs else None

        health["pulse"] = compute_pulse(root) if health["queue_root_exists"] else {}

        capabilities = {
            "worker_bridge": os.path.isfile(os.path.join(TOOLS, "clearwright_worker.py")),
            "proof_tool": os.path.isfile(os.path.join(TOOLS, "clearwright_proof.py")),
            "codex_helper": os.path.isfile(os.path.join(TOOLS, "clearwright_codex_review.py")),
            "gpt_helper": os.path.isfile(os.path.join(TOOLS, "clearwright_gpt_review.py")),
            "council_available": os.path.isfile(os.path.join(TOOLS, "clearwright_review_council.py")),
        }
        capabilities["codex_cli_on_path"] = codex_check() if codex_check else None
        # Review Council readiness: report only a boolean for the key (never the
        # value) and the configured model id. This never invokes GPT or Codex.
        capabilities["openai_api_key_configured"] = bool(key_check()) if key_check else None
        try:
            import clearwright_gpt_review as _gpt
            capabilities["configured_gpt_model"] = _gpt.resolve_model()
        except Exception:  # noqa: BLE001
            capabilities["configured_gpt_model"] = None
        health["capabilities"] = capabilities
        if not capabilities["worker_bridge"] or not capabilities["proof_tool"]:
            errors.append("Worker bridge or proof tool is missing from tools/.")
        if capabilities["codex_helper"] and capabilities["codex_cli_on_path"] is False:
            warnings.append("Codex CLI is not on PATH; Codex reviews are unavailable "
                            "(capability check only, not participation).")

        if mode != OPERATOR_MODE:
            warnings.append("Running in demo mode, not the live operator workspace.")
    except Exception as exc:  # noqa: BLE001 - health must degrade, not crash
        errors.append("Health check failed: {}".format(exc))

    health["status"] = "red" if errors else ("yellow" if warnings else "green")
    health["ok"] = health["status"] != "red"
    return health


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #

QUEUE_ROOT = None  # set in main()
DURABLE = False    # True when running against a persistent --queue-root
MODE = DEMO_MODE   # "operator" (live local) or "demo" (walkthrough); set in main()
DEMO_SEEDED = False  # True when demo packets were seeded into this queue

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ClearWrightControlPlaneDemo/0.1"

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel):
        # Restrict to files directly inside the static directory.
        name = os.path.basename(rel)
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, code=404)
            return
        ext = os.path.splitext(name)[1]
        ctype = STATIC_TYPES.get(ext, "application/octet-stream")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_static("index.html")
            return
        if path == "/api/state":
            self._send_json(build_state(QUEUE_ROOT))
            return
        if path == "/api/audit":
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            import urllib.parse
            filename = urllib.parse.unquote(params.get("filename", ""))
            self._send_json(build_audit(QUEUE_ROOT, filename))
            return
        if path == "/api/agent-events":
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            packet_id = urllib.parse.unquote(params.get("packet_id", "")) or None
            limit_raw = params.get("limit", "")
            limit = int(limit_raw) if limit_raw.isdigit() else None
            self._send_json({"events": cwae.read_events(QUEUE_ROOT, packet_id, limit)})
            return
        if path == "/api/messages":
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            packet_id = urllib.parse.unquote(params.get("packet_id", "")) or None
            thread_id = urllib.parse.unquote(params.get("thread_id", "")) or None
            limit_raw = params.get("limit", "")
            limit = int(limit_raw) if limit_raw.isdigit() else None
            # Binding-scoped single-record lookups for the composer's
            # post-write re-read and idempotency reconciliation: both REQUIRE
            # thread_id, so neither becomes a bare-id enumeration surface.
            message_id = urllib.parse.unquote(params.get("message_id", "")) or None
            idem_key = urllib.parse.unquote(params.get("idempotency_key", "")) or None
            if message_id and thread_id:
                found = cwm.find_by_message_id(QUEUE_ROOT, thread_id, message_id)
                self._send_json({"found": found is not None, "message": found})
                return
            if idem_key and thread_id:
                found = cwm.find_by_idempotency_key(QUEUE_ROOT, thread_id, idem_key)
                self._send_json({"found": found is not None, "message": found})
                return
            messages = cwm.read_messages(
                QUEUE_ROOT, packet_id=packet_id, thread_id=thread_id, limit=limit)
            # Archive-aware fallback: active always wins; the archive is
            # consulted only when a specific thread_id was requested and the
            # active store returned nothing for it.
            if not messages and thread_id:
                messages = cwarch.read_archived_messages(QUEUE_ROOT, thread_id)
                if limit is not None and limit >= 0:
                    messages = messages[-limit:]
            self._send_json({"messages": messages})
            return
        if path == "/api/work-items":
            wi_query = self.path.split("?", 1)[1] if "?" in self.path else ""
            wi_params = dict(p.split("=", 1) for p in wi_query.split("&") if "=" in p)
            include = "all" if wi_params.get("include") == "all" else "nonterminal"
            self._send_json({
                "work_items": cww.derive_work_items(QUEUE_ROOT, include=include),
                "integrity_warnings": cww.integrity_warnings(QUEUE_ROOT),
            })
            return
        if path == "/api/worker-status":
            self._send_json(cww.worker_status(QUEUE_ROOT))
            return
        if path == "/api/health":
            self._send_json(build_health(QUEUE_ROOT))
            return
        if path == "/api/active-run":
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            thread_id = urllib.parse.unquote(params.get("thread_id", "")) or None
            self._send_json(build_active_run(QUEUE_ROOT, thread_id=thread_id))
            return
        if path in ("/api/runs", "/api/conversations"):
            # Conversations are the same derived durable-thread summaries as
            # runs, presented conversation-first. One derivation, no new store.
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            def q(name):
                return urllib.parse.unquote(params.get(name, "")) or None
            limit_raw = params.get("limit", "")
            limit = int(limit_raw) if limit_raw.isdigit() else None
            has_codex_raw = q("has_codex")
            has_codex = None
            if has_codex_raw is not None:
                has_codex = has_codex_raw.lower() in ("1", "true", "yes")
            rows = build_runs(
                QUEUE_ROOT, limit=limit, status=q("status"), actor=q("actor"),
                source=q("source"), has_codex=has_codex, packet_id=q("packet_id"))
            key = "conversations" if path == "/api/conversations" else "runs"
            self._send_json({key: rows})
            return
        if path == "/api/history":
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            def q(name):
                return urllib.parse.unquote(params.get(name, "")) or None
            self._send_json(build_history(
                QUEUE_ROOT, packet_id=q("packet_id"), thread_id=q("thread_id"),
                actor=q("actor"), lane=q("lane"), status=q("status")))
            return
        if path == "/api/ledger":
            # Read-only unified History ledger; ?scope=active|archived|all.
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            scope = urllib.parse.unquote(params.get("scope", "")) or "active"
            if scope not in ("active", "archived", "all"):
                scope = "active"
            self._send_json(build_ledger(QUEUE_ROOT, scope=scope))
            return
        if path == "/api/task-state":
            # Read-only selected-task header + phase model, bound to a canonical
            # work item. work_item_id is the primary selector; thread_id is
            # accepted for compatibility.
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            thread_id = urllib.parse.unquote(params.get("thread_id", "")) or None
            wid = urllib.parse.unquote(params.get("work_item_id", "")) or None
            self._send_json(build_task_state(QUEUE_ROOT, thread_id=thread_id,
                                             work_item_id=wid))
            return
        if path == "/api/archive-index":
            self._send_json(build_archive_index_summary(QUEUE_ROOT))
            return
        if path == "/api/review-councils":
            # Read-only. The web server never runs GPT or Codex; it only reads
            # durable council state produced by the CLI/helper.
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            thread_id = urllib.parse.unquote(params.get("thread_id", "")) or None
            self._send_json({"review_councils": cwrc.list_councils(QUEUE_ROOT, thread_id=thread_id)})
            return
        if path == "/api/review-council":
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            council_id = urllib.parse.unquote(params.get("id", "")) or None
            full = cwrc.get_council(QUEUE_ROOT, council_id) if council_id else None
            source = "active"
            if full is None and council_id:
                full = cwarch.read_archived_council(QUEUE_ROOT, council_id)
                source = "archive"
            if full is None:
                self._send_json({"error": "council not found"}, code=404)
                return
            self._send_json(dict(full, source=source))
            return
        if path == "/api/work-summary":
            # Read-only: the harness-generated canonical summary for a work item.
            import urllib.parse
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            wid = urllib.parse.unquote(params.get("work_item_id", "")) or ""
            mid = wid.split(":", 1)[1] if ":" in wid else wid
            summary = load_json_safe(os.path.join(QUEUE_ROOT, "summaries", mid + ".json")) \
                if mid else None
            source = "active"
            if summary is None and mid:
                summary = cwarch.read_archived_summary(QUEUE_ROOT, mid)
                source = "archive"
            if summary is None:
                self._send_json({"error": "no summary recorded"}, code=404)
                return
            self._send_json({"summary": summary, "source": source})
            return
        if path.startswith("/static/") or path in ("/app.js", "/style.css"):
            self._send_static(path)
            return
        self._send_json({"error": "not found"}, code=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        # Content-Length framing: absent -> 411; non-numeric -> 400; over the
        # bounded request cap -> 413, all BEFORE reading a single body byte.
        # The bound is REQUEST_MAX_BYTES, independent of and larger than
        # MESSAGE_MAX_BYTES (worst-case JSON string-escape expansion of a
        # max-size message plus a fixed envelope allowance), so it admits
        # every supported canonical content value without an unbounded read.
        cl_header = self.headers.get("Content-Length")
        te_header = self.headers.get("Transfer-Encoding")
        if te_header and cl_header is None:
            self._send_json({"ok": False, "error": "chunked_transfer_not_supported"}, code=411)
            self.close_connection = True
            return
        if cl_header is None:
            length = 0
        else:
            try:
                length = int(cl_header)
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "invalid_content_length"}, code=400)
                self.close_connection = True
                return
            if length < 0:
                self._send_json({"ok": False, "error": "invalid_content_length"}, code=400)
                self.close_connection = True
                return
        if length > REQUEST_MAX_BYTES:
            self._send_json({"ok": False, "error": "request_too_large",
                             "limit_bytes": REQUEST_MAX_BYTES}, code=413)
            self.close_connection = True
            return
        # Bounded read: never more than REQUEST_MAX_BYTES, and never blocks
        # indefinitely waiting for bytes the client declared but never sent.
        self.connection.settimeout(30)
        try:
            raw = self.rfile.read(length) if length else b""
        except (OSError, ValueError):
            self._send_json({"ok": False, "error": "incomplete_body"}, code=400)
            self.close_connection = True
            return
        if len(raw) != length:
            self._send_json({"ok": False, "error": "incomplete_body"}, code=400)
            self.close_connection = True
            return
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "error": "invalid JSON body"}, code=400)
            return

        if path == "/api/reset":
            result = do_reset(QUEUE_ROOT, MODE)
            result["state"] = build_state(QUEUE_ROOT)
            self._send_json(result)
            return

        if path == "/api/action":
            action = payload.get("action")
            filename = payload.get("filename")
            reason = payload.get("reason", "")
            results = payload.get("results")
            result = do_action(QUEUE_ROOT, action, filename, reason, results)
            result["state"] = build_state(QUEUE_ROOT)
            self._send_json(result)
            return

        if path == "/api/request":
            result = do_request(QUEUE_ROOT, payload)
            result["state"] = build_state(QUEUE_ROOT)
            self._send_json(result)
            return

        if path == "/api/converse":
            self._send_json(build_conversation(payload.get("question", "")))
            return

        if path == "/api/agent-events":
            result = do_agent_event(QUEUE_ROOT, payload)
            self._send_json(result, code=200 if result.get("ok") else 400)
            return

        if path == "/api/messages":
            result = do_message(QUEUE_ROOT, payload, respond=False)
            self._send_json(result, code=_message_status_code(result))
            return

        if path == "/api/messages/respond":
            result = do_message(QUEUE_ROOT, payload, respond=True)
            self._send_json(result, code=_message_status_code(result))
            return

        if path == "/api/work-items/claim":
            result = cww.claim_work_item(
                QUEUE_ROOT, payload.get("work_item_id"), payload.get("actor"),
                role=payload.get("role") or cwm.DEFAULT_ROLE,
                source=payload.get("source") or "local-http")
            self._send_json(result, code=wi_status_code(result))
            return

        if path == "/api/work-items/progress":
            result = cww.progress_work_item(
                QUEUE_ROOT, payload.get("work_item_id"), payload.get("actor"),
                payload.get("message"), role=payload.get("role") or cwm.DEFAULT_ROLE,
                source=payload.get("source") or "local-http")
            self._send_json(result, code=wi_status_code(result))
            return

        if path == "/api/work-items/respond":
            result = cww.respond_work_item(
                QUEUE_ROOT, payload.get("work_item_id"), payload.get("actor"),
                payload.get("message"), role=payload.get("role") or cwm.DEFAULT_ROLE,
                source=payload.get("source") or "local-http")
            self._send_json(result, code=wi_status_code(result))
            return

        self._send_json({"ok": False, "error": "not found"}, code=404)

    def log_message(self, fmt, *args):
        # Keep the console quiet; a demo does not need per-request logging.
        return


def main():
    global QUEUE_ROOT, DURABLE, MODE, DEMO_SEEDED
    parser = argparse.ArgumentParser(description="ClearWright local control plane (early alpha).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1, local only).")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default 8787).")
    parser.add_argument(
        "--queue-root", default=None, metavar="PATH",
        help="Run against a durable clearance queue at PATH instead of a fresh "
             "temporary one. The directory and its lanes are created if missing; "
             "existing packets are never cleared or overwritten, and the queue is "
             "not removed on exit. A --queue-root defaults to operator mode.")
    parser.add_argument(
        "--mode", default=None, choices=[OPERATOR_MODE, DEMO_MODE],
        help="operator = live local operator workspace (no demo seeding, no "
             "reset). demo = local walkthrough (seeds demo packets into an empty "
             "queue, simulated feed, reset enabled). Default: operator when "
             "--queue-root is given, otherwise demo.")
    args = parser.parse_args()

    QUEUE_ROOT, DURABLE, MODE, DEMO_SEEDED = resolve_queue(args.queue_root, args.mode)
    if not DURABLE:
        # Only a fresh temporary queue is removed on exit; a durable queue
        # must persist.
        atexit.register(lambda: shutil.rmtree(QUEUE_ROOT, ignore_errors=True))

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://{}:{}/".format(args.host, args.port)
    print("ClearWright control plane ({} mode, local reference implementation, early alpha)".format(MODE))
    print("Queue root: {} ({}{})".format(
        QUEUE_ROOT, "durable" if DURABLE else "temporary",
        ", demo-seeded" if DEMO_SEEDED else ""))
    print("Open: {}".format(url))
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
