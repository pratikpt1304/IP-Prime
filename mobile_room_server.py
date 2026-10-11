"""
mobile_room_server.py — Prime AI Mobile Remote Voice Cockpit Server.
Serves the mobile-friendly holographic voice UI on port 8765.
Allows Pratik to control Prime AI directly from any smartphone or tablet on local WiFi.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_FILE = PROJECT_ROOT / "mobile_room.html"
OFFICE_FILE = PROJECT_ROOT / "office_floor.html"

app = Flask(__name__, static_folder=str(PROJECT_ROOT))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prime.mobile_room")

# LiveKit support (optional)
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
ROOM_NAME = "prime-ai-room"


def get_local_ip() -> str:
    """Detect LAN IPv4 address for phone connectivity."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


@app.route("/")
def index():
    """Serve the Prime Mobile Voice Cockpit UI."""
    if STATIC_FILE.exists():
        return STATIC_FILE.read_text(encoding="utf-8")
    return "<h3>mobile_room.html not found</h3>", 404


@app.route("/token")
def token():
    """Generate LiveKit token if configured, or report direct mode."""
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        return jsonify({
            "mode": "direct",
            "url": None,
            "token": None,
            "room": ROOM_NAME,
            "message": "LiveKit credentials not configured. Using direct neural API mode."
        })

    identity = f"operator-{str(uuid.uuid4())[:8]}"
    try:
        import jwt as pyjwt
        now = int(time.time())
        payload = {
            "iss": LIVEKIT_API_KEY,
            "sub": identity,
            "iat": now,
            "exp": now + 3600,
            "nbf": now,
            "jti": str(uuid.uuid4()),
            "video": {
                "room": ROOM_NAME,
                "roomJoin": True,
                "canPublish": True,
                "canSubscribe": True,
                "canPublishData": True,
            }
        }
        tok = pyjwt.encode(payload, LIVEKIT_API_SECRET, algorithm="HS256")
        token_str = tok if isinstance(tok, str) else tok.decode("utf-8")
        return jsonify({
            "mode": "livekit",
            "url": LIVEKIT_URL,
            "token": token_str,
            "room": ROOM_NAME,
            "identity": identity,
        })
    except Exception as e:
        logger.warning("Could not generate LiveKit token: %s", e)
        return jsonify({"mode": "direct", "error": str(e)})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Direct Neural API endpoint for mobile voice/text input.
    Executes commands on the PC using Prime AI Agent and returns the response.
    """
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Empty message."}), 400

    try:
        from ai_agent import agent

        tool_logs = []

        def on_tool(name, args):
            tool_logs.append(f"Ran tool: {name}")

        reply = agent.process_message(message, on_tool_call=on_tool)
        return jsonify({
            "ok": True,
            "reply": reply,
            "tools": tool_logs,
        })
    except Exception as e:
        logger.exception("Error processing mobile chat: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/command", methods=["POST"])
def api_command():
    """Execute raw voice or text command directly."""
    data = request.get_json(force=True, silent=True) or {}
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "Empty command."}), 400
    try:
        from ai_agent import agent
        reply = agent.process_message(cmd)
        return jsonify({"ok": True, "reply": reply})
    except Exception as e:
        logger.exception("Error executing command: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/gesture", methods=["POST"])
def api_gesture():
    """Handle touchless hand gesture commands (MediaPipe)."""
    data = request.get_json(force=True, silent=True) or {}
    gesture = str(data.get("gesture", "")).strip().lower()
    from tool_definitions import execute_tool

    if gesture in ("open_palm", "mute", "barge_in"):
        try:
            from voice_engine import voice
            voice.barge_in()
        except Exception:
            pass
        res = execute_tool("muteToggle", {})
        return jsonify({"ok": True, "gesture": gesture, "action": "barge_in_and_mute", "result": res})

    elif gesture in ("thumbs_up", "confirm", "proceed"):
        res = execute_tool("confirmAndSendWhatsAppDraft", {})
        return jsonify({"ok": True, "gesture": gesture, "action": "confirm_draft", "result": res})

    elif gesture in ("pinch", "rotate", "spread", "zoom"):
        return jsonify({"ok": True, "gesture": gesture, "action": "viewport_transform"})

    elif gesture in ("next_track", "swipe_right"):
        res = execute_tool("mediaControl", {"action": "next"})
        return jsonify({"ok": True, "gesture": gesture, "action": "media_next", "result": res})

    elif gesture in ("prev_track", "swipe_left"):
        res = execute_tool("mediaControl", {"action": "previous"})
        return jsonify({"ok": True, "gesture": gesture, "action": "media_prev", "result": res})

    return jsonify({"ok": False, "error": f"Unknown gesture: {gesture}"}), 400


from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
from neural_mesh_bridge import mesh_bridge

# Start background PC clipboard watcher
mesh_bridge.start_clipboard_sync()

@app.route("/api/mesh/pair")
def api_mesh_pair():
    """Return local LAN pairing credentials."""
    ip = get_local_ip()
    return jsonify({
        "ok": True,
        "token": mesh_bridge.auth_token,
        "lan_ip": ip,
        "port": 8765,
        "connect_url": f"http://{ip}:8765/?pin={mesh_bridge.auth_token}",
    })

@app.route("/api/mesh/clipboard", methods=["GET", "POST"])
def api_mesh_clipboard():
    """Bidirectional clipboard endpoint for mobile synchronization."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        text = data.get("text", "")
        if text:
            success = mesh_bridge.set_pc_clipboard(text)
            return jsonify({"ok": success})
        return jsonify({"ok": False, "error": "Empty text"}), 400
    else:
        text = mesh_bridge._get_pc_clipboard()
        return jsonify({"ok": True, "text": text})

@app.route("/api/mesh/action", methods=["POST"])
def api_mesh_action():
    """Execute quick hardware actions remotely from phone."""
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "")
    params = data.get("params", {})
    res = mesh_bridge.execute_remote_action(action, params)
    return jsonify(res)

@app.route("/api/mesh/events")
def api_mesh_events():
    """Real-time Server-Sent Events (SSE) stream to connected mobile devices."""
    def event_stream():
        q = mesh_bridge.subscribe_events()
        try:
            # Yield initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'token_valid': True})}\n\n"
            while True:
                try:
                    event = q.get(timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except Exception:
                    # Keep-alive heartbeat
                    yield f"data: {json.dumps({'type': 'heartbeat', 'time': time.time()})}\n\n"
        finally:
            mesh_bridge.unsubscribe_events(q)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")


@app.route("/api/status")
def api_status():
    """Telemetry endpoint for mobile cockpit status."""
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        from tool_definitions import TOOL_SPECS
        return jsonify({
            "status": "online",
            "cpu_percent": cpu,
            "ram_percent": mem,
            "tools_count": len(TOOL_SPECS),
            "operator": "Pratik Thorat",
            "mesh_token": mesh_bridge.auth_token,
        })
    except Exception as e:
        return jsonify({"status": "unknown", "error": str(e)})


@app.route("/api/hud/state")
def api_hud_state():
    """
    Consolidated HUD state endpoint providing live hardware, cognitive agent status,
    HER coral aura visualizer parameters, active durable plan, and Friday receipts.
    """
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
    except Exception:
        cpu, mem = 0.0, 0.0

    # 1. Visualizer (HER Coral Breathing State)
    visualizer_data = {}
    try:
        from actions.her_companion import get_her_visualizer_state
        visualizer_data = get_her_visualizer_state()
    except Exception as e:
        visualizer_data = {"error": str(e)}

    # 2. Durable Task Plan
    active_plan = None
    try:
        from actions.friday_tasks import get_active_task_plan
        plan_res = get_active_task_plan()
        if plan_res.get("has_active_plan"):
            active_plan = plan_res.get("plan")
    except Exception:
        pass

    # 3. Recent Execution Receipts
    recent_receipts = []
    try:
        from actions.friday_receipts import get_execution_receipts
        rec_res = get_execution_receipts(limit=5)
        recent_receipts = rec_res.get("receipts", [])
    except Exception:
        pass

    # 4. User Preferences Memory (via Master Brain)
    preferences_count = 0
    dots_telemetry = {}
    running_dots = []
    try:
        from core.master_brain import prime_brain
        preferences_count = len(prime_brain._ram_cache)
    except Exception:
        pass

    try:
        from core.prime_dots import dot_engine
        dots_telemetry = dot_engine.get_telemetry()
        running_dots = [d for d in dot_engine.list_dots() if d.get("status") in ("running", "waiting_approval")]
    except Exception:
        pass

    from tool_definitions import TOOL_SPECS
    from config import config

    return jsonify({
        "ok": True,
        "status": "online",
        "timestamp": time.time(),
        "hardware": {
            "cpu_percent": cpu,
            "ram_percent": mem,
        },
        "ai": {
            "active_provider": config.get_active_provider(),
            "tools_count": len(TOOL_SPECS),
            "operator": "Pratik Thorat",
            "preferences_count": preferences_count,
            "dots_telemetry": dots_telemetry,
        },
        "visualizer": visualizer_data,
        "active_plan": active_plan,
        "recent_receipts": recent_receipts,
        "active_dots": running_dots,
    })


@app.route("/api/hud/stream")
def api_hud_stream():
    """Real-time SSE stream of HUD telemetry and HER breathing visualizer frames."""
    def hud_event_stream():
        import psutil
        try:
            from actions.her_companion import get_her_visualizer_state
            from actions.friday_tasks import get_active_task_plan
            from actions.friday_receipts import get_execution_receipts
            from core.prime_dots import dot_engine
            from config import config
            from tool_definitions import TOOL_SPECS

            while True:
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
                vis = get_her_visualizer_state()
                plan_res = get_active_task_plan()
                active_plan = plan_res.get("plan") if plan_res.get("has_active_plan") else None
                rec_res = get_execution_receipts(limit=3)
                try:
                    dots_telemetry = dot_engine.get_telemetry()
                except Exception:
                    dots_telemetry = {}

                payload = {
                    "type": "hud_tick",
                    "time": time.time(),
                    "cpu": cpu,
                    "ram": mem,
                    "visualizer": vis,
                    "active_plan": active_plan,
                    "recent_receipts": rec_res.get("receipts", []),
                    "dots_telemetry": dots_telemetry,
                    "tools_count": len(TOOL_SPECS),
                    "provider": config.get_active_provider(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                time.sleep(1.5)
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream_with_context(hud_event_stream()), mimetype="text/event-stream")


@app.route("/api/dots", methods=["GET", "POST"])
def api_dots():
    """
    Prime Dots Mobile API:
    GET: returns all registered dots and telemetry summary.
    POST: allows spawning, pausing, resuming, stopping, or approving background dots.
    """
    from core.prime_dots import dot_engine

    if request.method == "GET":
        dots = dot_engine.list_dots()
        telemetry = dot_engine.get_telemetry()
        return jsonify({
            "ok": True,
            "telemetry": telemetry,
            "dots": dots,
        })

    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "").lower().strip()
    dot_id = data.get("dot_id", "").strip()

    if action == "spawn":
        goal = data.get("goal", "").strip()
        if not goal:
            return jsonify({"ok": False, "error": "Goal is required to spawn a Dot."}), 400
        name = data.get("name")
        interval = int(data.get("interval_seconds", 0))
        res = dot_engine.spawn_dot(goal=goal, name=name, interval_seconds=interval)
        return jsonify(res)

    if not dot_id:
        return jsonify({"ok": False, "error": "dot_id is required for this action."}), 400

    if action == "pause":
        ok = dot_engine.pause_dot(dot_id)
        return jsonify({"ok": ok, "message": f"Dot {dot_id} paused." if ok else f"Dot {dot_id} not found."})

    elif action == "resume":
        ok = dot_engine.resume_dot(dot_id)
        return jsonify({"ok": ok, "message": f"Dot {dot_id} resumed." if ok else f"Dot {dot_id} not found."})

    elif action == "stop":
        ok = dot_engine.stop_dot(dot_id)
        return jsonify({"ok": ok, "message": f"Dot {dot_id} stopped." if ok else f"Dot {dot_id} not found."})

    elif action in ("approve", "reject"):
        approved = (action == "approve")
        ok = dot_engine.approve_dot_action(dot_id, approved=approved)
        return jsonify({"ok": ok, "message": f"Action for {dot_id} {'approved' if approved else 'rejected'}." if ok else f"Dot {dot_id} not found."})

    elif action in ("read_canvas", "canvas"):
        dot = dot_engine.get_dot(dot_id)
        if not dot:
            return jsonify({"ok": False, "error": f"Dot {dot_id} not found."}), 404
        content = dot.get_canvas_content()
        return jsonify({"ok": True, "dot_id": dot_id, "canvas": content})

    return jsonify({"ok": False, "error": f"Unknown action: '{action}'"}), 400


@app.route("/office")
def office_view():
    """Serve the 2D Virtual Office Floor UI."""
    if OFFICE_FILE.exists():
        return OFFICE_FILE.read_text(encoding="utf-8")
    return "<h3>office_floor.html not found</h3>", 404


@app.route("/api/office/state")
def office_state():
    """Return complete 2D office state including dots, envelope flights, stapler status, and DLP telemetry."""
    from core.prime_dots import dot_engine
    from core.dots_mailbox import mailbox_router
    from core.dlp_shield import dlp_shield

    try:
        from actions.stapler_dictation import stapler_engine
        stapler_enabled = stapler_engine.enabled
    except Exception:
        stapler_enabled = False

    dots_data = dot_engine.list_dots()
    # Enrich each dot with unread inbox count
    for d in dots_data:
        dot = dot_engine.get_dot(d["dot_id"])
        d["unread_inbox"] = dot.mailbox.get_unread_count() if dot else 0

    flights = mailbox_router.get_recent_flights(seconds_window=45.0)
    telemetry = dot_engine.get_telemetry()

    return jsonify({
        "ok": True,
        "dots": dots_data,
        "flights": flights,
        "telemetry": telemetry,
        "stapler": {
            "enabled": stapler_enabled,
            "hotkey": "Ctrl+Alt+Space",
        },
        "dlp": dlp_shield.get_telemetry(),
    })


@app.route("/api/dlp/status")
def dlp_status():
    """Return Zero-Leakage DLP & PII Redaction Shield telemetry."""
    from core.dlp_shield import dlp_shield
    return jsonify({"ok": True, "dlp": dlp_shield.get_telemetry()})


@app.route("/api/dlp/scan", methods=["POST"])
def dlp_scan():
    """Test or execute Zero-Leakage PII & UAE compliance sanitization on input text."""
    from core.dlp_shield import dlp_shield
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "")
    sanitized, detections = dlp_shield.sanitize_text(text)
    return jsonify({
        "ok": True,
        "sanitized": sanitized,
        "detections": detections,
        "dlp": dlp_shield.get_telemetry(),
    })


CHAMPIONSHIP_PRESETS = {
    "workos": {
        "edition": "PRIME WorkOS — Autonomous SME Digital Workforce (Youth / Business Category)",
        "commander_title": "PRIME WORKOS :: DUBAI SME COMMAND CENTER",
        "agents": [
            ("Trade & Customs Agent", "Audit commercial invoices, verify GCC HS codes, and prepare Dubai Trade Mirsal-2 customs declarations."),
            ("WhatsApp CRM Concierge", "Respond to customer inquiries & voice calls in Arabic, English, and Hindi with instant order lookups."),
            ("Procurement Negotiator", "Request supplier quotes via WhatsApp, compare pricing, and route Purchase Orders for HITL approval."),
            ("FinOps & UAE VAT Auditor", "Reconcile daily invoices, calculate 5% UAE VAT & 9% Corporate Tax reserves, and flag anomalies."),
            ("SecOps & DLP Sentinel", "Enforce Zero-Leakage PII redaction (Emirates ID, IBAN, API keys) and AST code execution guardrails."),
            ("DevOps & QA Automator", "Monitor terminal logs, run 22-suite regression matrix, and self-heal microservice deployments."),
        ],
    },
    "access": {
        "edition": "PRIME Access — Zero-GUI Hands-Free OS for People of Determination (Societal Category)",
        "commander_title": "PRIME ACCESS :: HANDS-FREE ACCESSIBILITY COCKPIT",
        "agents": [
            ("Voice UI Navigator", "Execute 100% hands-free mouse clicks, window focus, and browser navigation via ambient voice."),
            ("Stapler Dictation Engine", "Provide zero-latency system-wide voice dictation (Ctrl+Alt+Space) in Arabic, English, and Hindi."),
            ("Multimodal Screen Reader", "Analyze screen layouts and read complex visual interfaces or error dialogs aloud for visually/motor-impaired users."),
            ("Hands-Free Communicator", "Place, answer, and schedule WhatsApp voice/video calls and messages without touching a keyboard."),
            ("Autonomous Code Copilot", "Write, patch, debug, and commit software engineering projects purely by voice instructions."),
            ("Voice HITL Safety Gate", "Protect the operator from accidental destructive actions with trilingual spoken confirmation."),
        ],
    },
}


@app.route("/api/championship/preset", methods=["POST"])
def apply_championship_preset():
    """Deploy a pre-configured multi-agent swarm for the Dubai AI Agents Championship live demo."""
    from core.prime_dots import dot_engine, PrimeDot
    from core.dots_mailbox import mailbox_router
    from core.dlp_shield import dlp_shield

    data = request.get_json(force=True, silent=True) or {}
    mode = str(data.get("mode", "workos")).lower().strip()
    if mode not in CHAMPIONSHIP_PRESETS:
        return jsonify({"ok": False, "error": f"Unknown preset mode: {mode}"}), 400

    preset = CHAMPIONSHIP_PRESETS[mode]
    # Stop and clear existing demo dots to keep the floor crisp
    for did in list(dot_engine.dots.keys()):
        dot_engine.stop_dot(did)
    dot_engine.dots.clear()

    spawned_ids = []
    for idx, (agent_name, goal) in enumerate(preset["agents"], start=1):
        dot_id = f"dot_{mode}_{idx:02d}"
        dot = PrimeDot(goal=goal, name=agent_name, dot_id=dot_id, interval_seconds=0)
        dot.status = "running" if idx != 3 else "waiting_approval"
        if idx == 3:
            dot.pending_approval = {
                "token": f"hitl_{mode}_{idx}",
                "tool": "sendWhatsAppMessage" if mode == "workos" else "executePowerShell",
                "args": {"recipient": "Dubai Supplier #4", "amount_aed": 14500},
                "reason": "High-value action gated by Voice-First HITL Policy (Say 'Approve' or 'موافق').",
                "timestamp": "LIVE",
            }
        dot._log(f"Initialized in {preset['edition']}")
        dot.findings.append(f"Active & verified under UAE PDPL Zero-Leakage Guardrail.")
        dot_engine.dots[dot_id] = dot
        spawned_ids.append(dot_id)

    dot_engine._save_state()

    # Trigger stigmergic mailbox message flight across desks for visual demonstration
    if len(spawned_ids) >= 3:
        d1 = dot_engine.get_dot(spawned_ids[0])
        d2 = dot_engine.get_dot(spawned_ids[1])
        if d1 and d2:
            d1.send_message(spawned_ids[1], "Task Handshake", "Verified compliance packet ready for dispatch.")
            d2.send_message(spawned_ids[2], "Approval Request", "Queued supplier order for operator HITL sign-off.")
            mailbox_router.dispatch_pending()

    # Run a sample DLP scan so the shield counter demonstrates live protection
    dlp_shield.sanitize_text(
        "Sample audit: Emirates ID 784-1999-1234567-1 and IBAN AE070331234567890123456 verified."
    )

    return jsonify({
        "ok": True,
        "mode": mode,
        "edition": preset["edition"],
        "commander_title": preset["commander_title"],
        "spawned_count": len(spawned_ids),
    })


@app.route("/api/stapler", methods=["GET", "POST"])
def stapler_control():
    """Query or toggle system-wide Stapler dictation hotkey."""
    from actions.stapler_dictation import stapler_engine
    if request.method == "GET":
        return jsonify({"enabled": stapler_engine.enabled, "hotkey": "Ctrl+Alt+Space"})
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "toggle").lower()
    if action == "start":
        stapler_engine.start()
    elif action == "stop":
        stapler_engine.stop()
    elif action == "toggle":
        stapler_engine.toggle()
    elif action == "dictate":
        res = stapler_engine.record_and_inject()
        return jsonify(res)
    return jsonify({"ok": True, "enabled": stapler_engine.enabled})


@app.route("/api/dots/message", methods=["POST"])
def send_dot_message():
    """Send asynchronous stigmergic message between Dots via Mailbox protocol."""
    from core.prime_dots import dot_engine
    data = request.get_json(force=True, silent=True) or {}
    sender = data.get("sender")
    recipient = data.get("recipient")
    subject = data.get("subject", "Task Update")
    body = data.get("body", "")
    if not (sender and recipient and body):
        return jsonify({"ok": False, "error": "sender, recipient, and body required"}), 400

    s_dot = dot_engine.get_dot(sender)
    if not s_dot:
        return jsonify({"ok": False, "error": f"Sender dot '{sender}' not found"}), 404

    msg = s_dot.send_message(recipient, subject, body)
    return jsonify({"ok": True, "message": msg})



def run_server(port: int = 8765):
    ip = get_local_ip()
    print("=" * 60)
    print("  PRIME AI :: MOBILE VOICE COCKPIT SERVER ONLINE")
    print(f"  Phone/Tablet URL:  http://{ip}:{port}")
    print(f"  Local PC URL:      http://localhost:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
    run_server(port_arg)
