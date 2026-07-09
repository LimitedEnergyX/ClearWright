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
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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
    defaults to an outbound response. Returns a result dict."""
    fields = payload if isinstance(payload, dict) else {}
    thread_id = fields.get("thread_id")
    if respond and not (thread_id and str(thread_id).strip()):
        return {"ok": False, "error": "respond requires a thread_id"}
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
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, message)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": message, "thread_id": message["thread_id"]}


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
    return {
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


PULSE_RECENCY_SECONDS = 300  # 5 minutes: how recent a completion still pulses.


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

    open_items = any(i.get("status") == "open" for i in items)
    claimed_items = any(i.get("status") == "claimed" for i in items)
    recent_progress = any(_within(m.get("at"), now, PULSE_RECENCY_SECONDS)
                          for m in messages if m.get("direction") == "internal")
    recent_response = any(_within(m.get("at"), now, PULSE_RECENCY_SECONDS)
                          for m in messages
                          if m.get("direction") == "outbound" or m.get("status") == "responded")
    recent_done_packet = False
    for pkt in done_packets:
        evs = audit_events(pkt)
        if evs and _within(evs[-1].get("at"), now, PULSE_RECENCY_SECONDS):
            recent_done_packet = True
            break

    return {
        "incoming": open_items or any(s in ("RTA", "IN_REVIEW", "RFI_PENDING", "CTA") for s in outbox_status),
        "decision": any(s in ("RTA", "IN_REVIEW") for s in outbox_status),
        "cta": any(s == "CTA" for s in outbox_status),
        "rfi": any(s == "RFI_PENDING" for s in outbox_status),
        "claimed": claimed_items or inprog_count > 0 or recent_progress,
        "verify": recent_progress,
        "done": recent_response or recent_done_packet,
    }


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
    lanes = {lane: [] for lane in LANES}
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            try:
                lanes[lane].append(packet_summary(os.path.join(lane_dir, name), lane))
            except (OSError, ValueError):
                lanes[lane].append({"filename": name, "lane": lane, "status": "UNREADABLE"})
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
    if path is None:
        return {"filename": filename, "found": False, "events": []}
    packet = load_json(path)
    return {
        "filename": os.path.basename(path),
        "found": True,
        "lane": lane,
        "packet_id": packet.get("packet_id"),
        "status": packet.get("status"),
        "events": audit_events(packet),
    }


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
            self._send_json({"messages": cwm.read_messages(
                QUEUE_ROOT, packet_id=packet_id, thread_id=thread_id, limit=limit)})
            return
        if path == "/api/work-items":
            self._send_json({"work_items": cww.derive_work_items(QUEUE_ROOT)})
            return
        if path == "/api/worker-status":
            self._send_json(cww.worker_status(QUEUE_ROOT))
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
        if path.startswith("/static/") or path in ("/app.js", "/style.css"):
            self._send_static(path)
            return
        self._send_json({"error": "not found"}, code=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
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
            self._send_json(result, code=200 if result.get("ok") else 400)
            return

        if path == "/api/messages/respond":
            result = do_message(QUEUE_ROOT, payload, respond=True)
            self._send_json(result, code=200 if result.get("ok") else 400)
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
