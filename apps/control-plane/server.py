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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
TOOLS = os.path.join(REPO_ROOT, "tools")
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


# --------------------------------------------------------------------------- #
# Demo queue management (temporary, outside the repository)
# --------------------------------------------------------------------------- #

def make_queue_root():
    """Create a fresh temporary queue root with the four canonical lanes."""
    root = tempfile.mkdtemp(prefix="clearwright_demo_")
    for lane in LANES:
        os.makedirs(os.path.join(root, lane), exist_ok=True)
    return root


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


def tool_argv(action, path, reason):
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
        return [lifecycle, "complete", path, "--actor", DEMO_ACTOR]
    if action == "fail":
        return [lifecycle, "fail", path, "--reason", reason, "--actor", DEMO_ACTOR]
    return None


def do_action(root, action, filename, reason=""):
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

    argv = tool_argv(action, path, reason)
    if argv is None:
        return {"ok": False, "error": "unknown action: {}".format(action), "output": ""}

    code, output = run_tool(argv)
    return {"ok": code == 0, "returncode": code, "output": output}


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
        "audit_event_count": len(audit_events(packet)),
        "allowed_actions": allowed_actions(status, lane),
    }


def build_state(root):
    """Return the full board: mission plus packets grouped by lane."""
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
    return {"mission": read_mission(), "lanes": lanes, "actor": DEMO_ACTOR}


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


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #

QUEUE_ROOT = None  # set in main()

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
            seed_queue(QUEUE_ROOT)
            self._send_json({"ok": True, "state": build_state(QUEUE_ROOT)})
            return

        if path == "/api/action":
            action = payload.get("action")
            filename = payload.get("filename")
            reason = payload.get("reason", "")
            result = do_action(QUEUE_ROOT, action, filename, reason)
            result["state"] = build_state(QUEUE_ROOT)
            self._send_json(result)
            return

        self._send_json({"ok": False, "error": "not found"}, code=404)

    def log_message(self, fmt, *args):
        # Keep the console quiet; a demo does not need per-request logging.
        return


def main():
    global QUEUE_ROOT
    parser = argparse.ArgumentParser(description="ClearWright local control plane demo (early alpha).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1, local only).")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default 8787).")
    args = parser.parse_args()

    QUEUE_ROOT = make_queue_root()
    seed_queue(QUEUE_ROOT)
    atexit.register(lambda: shutil.rmtree(QUEUE_ROOT, ignore_errors=True))

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://{}:{}/".format(args.host, args.port)
    print("ClearWright control plane demo (local reference implementation, early alpha)")
    print("Demo queue root: {}".format(QUEUE_ROOT))
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
