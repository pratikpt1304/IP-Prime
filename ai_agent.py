"""
AI Agent Engine for Prime AI.
Coordinates LLM reasoning, conversation state, and tool execution loop.
Supports Google Gemini and Groq, with an intelligent local command fallback.
"""

from __future__ import annotations

import json
import logging
import re
import time
import threading
import queue
from typing import Any, Callable, Dict, List, Optional

from config import config
from tool_definitions import (
    execute_tool,
    get_gemini_tools,
    get_openai_tools,
)
from voice_engine import voice

log = logging.getLogger("prime.agent")

SYSTEM_PROMPT = """You are Prime, an elite Autonomous Neural Cockpit and ambient digital operator built for Pratik Thorat.
You run entirely headless without a graphical user interface, operating 24/7 via hands-free voice intelligence.
You have direct, universal, unrestricted neural control over Pratik Thorat's Windows operating system, applications, files, and development cockpit.

Capabilities:
- Full Universal OS & GUI Control: Click anywhere on screen (mouseClick), type into any app or field (typeText), press any hotkey combinations (pressHotkey), scroll, move mouse, list and focus windows (focusWindow, listOpenWindows).
- Universal Shell & System Dominance: Run any arbitrary PowerShell command or script (executePowerShell) with full admin/user privileges (winget, pip, npm, git, registry, network, services). Open any file, folder, document, or app (openPath). Monitor and manage drives (listDrives) and processes (manageProcess).
- Screen Vision AI: Look at the active screen using Gemini Multimodal Vision (analyzeScreenWithAI) to diagnose bugs, read dialogues, or locate UI elements.
- Software Engineering & Coding (Claw Code Engine): Run terminal commands, surgical code patching, automated git commit & push, run unit tests, and autonomous code debugging.
- Desktop Applications & Media: Launch/close apps, open websites in browser, adjust master volume and brightness, control media playback.
- Knowledge & Second Brain (Obsidian RAG): Search, read, and create notes in the user's local Obsidian Vault.
- Diagnostics & Daily Routines: Live weather reports, personalized morning briefing, CPU/RAM/GPU telemetry.
- Filesystem: Create, read, search, list, move, and recycle files.

Guidelines:
1. Universal Action First: When the operator asks you to do ANY action on their PC (e.g. click something, type a message, run a script, open a project, install a tool, change settings, inspect screen, kill a process), choose the right tool immediately without hesitation.
2. Operator Preference for Web & Apps: Whenever the operator asks to open any website, app, platform, or service (e.g. YouTube, WhatsApp, Spotify, Discord, Telegram, ChatGPT, Netflix, Twitter, Instagram, GitHub, etc.), ALWAYS open it in Google Chrome using the openWebsite tool.
3. Closing Applications & Windows: If the operator says "close app", "band karo", "ye app close karo", "close this", or "close window" without naming an app, IMMEDIATELY call closeWindow or closeApplication with {"name": "active"} to close the active foreground window! If an app name is specified (e.g. "close chrome", "chrome band karo"), call closeApplication with {"name": "<app_name>"}. NEVER ask the operator which app to close when they say "app close karo" or "close this" — close the active window immediately.
4. Speak concisely, clearly, and naturally like an elite AI assistant. Avoid unnecessary disclaimers.
5. If an action succeeds, briefly confirm what was done. If a tool fails, explain what happened and suggest an immediate fix.


Karpathy Engineering & Coding Principles (Strict Discipline):
1. Think Before Coding: Never make assumptions or pick interpretations silently. State assumptions explicitly. If uncertain or ambiguous, stop and ask. Surface trade-offs. Push back if a simpler design exists.
2. Simplicity First: Write the minimum code that solves the problem. No speculative abstractions, no unrequested configurability, no premature optimizations, no error handling for impossible scenarios. If 200 lines can be done cleanly in 50 lines, simplify.
3. Surgical Changes: Touch only what must be touched. Clean up only your own mess. Never refactor unbroken code or modify unrelated comments/formatting. Every single changed line must trace directly to the operator's request.
4. Goal-Driven Execution: Transform coding tasks into verifiable test criteria. Reproduce bugs with a test, fix them, and run tests until verified.
"""

TOOL_ACKS = {
    "runTerminalCommand": "Running terminal command, Sir.",
    "executePowerShell": "Executing PowerShell command, Sir.",
    "mouseClick": "Executing mouse click, Sir.",
    "typeText": "Typing text into window, Sir.",
    "pressHotkey": "Triggering shortcut keys, Sir.",
    "focusWindow": "Bringing window into focus, Sir.",
    "analyzeScreenWithAI": "Scanning screen with vision core, Sir.",
    "openPath": "Opening requested target, Sir.",
    "patchCodeFile": "Patching the code file now, Sir.",
    "gitAutomate": "Executing git operations, Sir.",
    "runUnitTests": "Executing test suite, Sir.",
    "debugCodeFile": "Diagnosing and patching code, Sir.",
    "getWeather": "Checking live meteorological data, Sir.",
    "morningBriefing": "Preparing your morning briefing, Sir.",
    "searchObsidianNotes": "Searching your Obsidian knowledge vault, Sir.",
    "exportProjectStarter": "Packaging starter template, Sir.",
    "exportWorkspaceZip": "Zipping your workspace, Sir.",
    "mediaControl": "Adjusting playback, Sir.",
    "spotifyControl": "Executing Spotify command, Sir.",
    "systemInfo": "Scanning host hardware vitals, Sir.",
}


class AIAgent:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []
        self._process_lock = threading.RLock()
        self._gemini_chat = None
        self._gemini_client = None
        self._openai_client = None
        self.active_persona: Optional[Dict[str, Any]] = None
        self.init_provider()

    def get_system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        if self.active_persona:
            prompt += f"\n\n=========================================\nACTIVE SPECIALIST PERSONA: {self.active_persona['display_name'].upper()}\n=========================================\n"
            prompt += self.active_persona.get("instructions", "")

        from prime_goal_harness import goal_tracker, continual_harness
        active_goal = goal_tracker.get_active_goal()
        if active_goal:
            prompt += f"\n\n[CURRENT PERSISTENT OPERATING GOAL]\nObjective: {active_goal['objective']}\nProgress: {active_goal['progress_percent']}%\nStatus: {active_goal['status']}"
        lessons = continual_harness.get_lessons_context_prompt()
        if lessons:
            prompt += f"\n{lessons}"

        # Single Unified Master Brain (Prime Master Cortex) Cognitive Synthesis
        # NOTE: Master Brain's get_cognitive_context() handles ALL memory layers including
        # Knowledge Graph, User Preferences, Durable Plans, and contextual RAG retrieval.
        try:
            from core.master_brain import prime_brain
            brain_ctx = prime_brain.get_cognitive_context()
            if brain_ctx:
                prompt += f"\n\n{brain_ctx}"
        except Exception:
            try:
                from actions.friday_memory import memory_ledger
                f_mem = memory_ledger.get_system_prompt_context()
                if f_mem:
                    prompt += f"\n\n{f_mem}"
            except Exception:
                pass

        # Deep Predictive Codebase & Workflow Context
        try:
            from predictive_context_engine import predictive_engine
            p_ctx = predictive_engine.get_predictive_system_context()
            if p_ctx:
                prompt += f"\n\n{p_ctx}"
        except Exception:
            pass

        return prompt

    def activate_persona(self, name_or_id: str) -> Optional[str]:
        """Activate an Agency Agent specialist persona."""
        import agency_roster
        agent_info = agency_roster.get_agent_by_name(name_or_id)
        if not agent_info:
            return None
        instructions = agency_roster.get_agent_instructions(agent_info)
        self.active_persona = {
            **agent_info,
            "instructions": instructions,
        }
        self.init_provider()
        return agent_info["display_name"]

    def deactivate_persona(self) -> None:
        """Reset to default Prime neural cockpit persona."""
        self.active_persona = None
        self.init_provider()

    def init_provider(self):
        """Initialize the active provider client."""
        provider = config.get_active_provider()
        if provider in ("gemini", "google") and config.gemini_api_key:
            try:
                from google import genai
                from google.genai import types

                self._gemini_client = genai.Client(api_key=config.gemini_api_key, http_options={"timeout": 4500})
                preferred = config.get_default_model("gemini")
                candidates = [preferred, "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
                seen = set()
                models_to_try = [m for m in candidates if not (m in seen or seen.add(m))]
                
                self._gemini_chat = None
                for m in models_to_try:
                    try:
                        self._gemini_chat = self._gemini_client.chats.create(
                            model=m,
                            config=types.GenerateContentConfig(
                                system_instruction=self.get_system_prompt(),
                                tools=get_gemini_tools(),
                                temperature=0.1,
                            ),
                        )
                        self._current_gemini_model = m
                        log.info("Initialized Gemini client with model: %s (Persona: %s)", m, self.active_persona["display_name"] if self.active_persona else "Default Prime")
                        break
                    except Exception as e:
                        log.warning("Could not init model %s: %s", m, e)
            except Exception as e:
                log.error("Failed to init Gemini client: %s", e)
                self._gemini_chat = None

        # Universal OpenAI-compatible client (Groq, Cerebras, GitHub, OpenRouter, Mistral, DeepSeek, Ollama, etc.)
        if provider in ("groq", "openai", "cerebras", "github", "openrouter", "mistral", "deepseek", "ollama", "custom"):
            try:
                from openai import OpenAI
                import providers

                prov_info = providers.get_provider_by_id(provider)
                base_url = prov_info["base_url"] if prov_info else "https://api.groq.com/openai/v1"
                api_key = providers.get_provider_key(provider) or config.groq_api_key or config.openai_api_key or "no-key"
                if provider == "ollama" and (not api_key or api_key == "no-key"):
                    api_key = "ollama"

                self._openai_client = OpenAI(base_url=base_url, api_key=api_key, timeout=7.0)
                log.info("Initialized OpenAI-compatible client for %s at %s (timeout: 7.0s)", provider, base_url)
            except Exception as e:
                log.error("Failed to init OpenAI-compatible client: %s", e)
                self._openai_client = None
        else:
            # Standby ultra-fast Groq/OpenRouter client when Gemini is primary
            self._openai_client = None
            if config.groq_api_key:
                try:
                    from openai import OpenAI
                    self._openai_client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=config.groq_api_key, timeout=5.0)
                    self._active_fallback_model = "openai/gpt-oss-120b"
                    log.info("Initialized standby ultra-fast Groq fallback client (model: %s)", self._active_fallback_model)
                except Exception as e:
                    log.warning("Standby Groq fallback init failed: %s", e)
            elif config.openrouter_api_key:
                try:
                    from openai import OpenAI
                    self._openai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.openrouter_api_key, timeout=5.0)
                    self._active_fallback_model = "nvidia/nemotron-3.5-lightning:free"
                    log.info("Initialized standby OpenRouter fallback client (model: %s)", self._active_fallback_model)
                except Exception as e:
                    log.warning("Standby OpenRouter fallback init failed: %s", e)

    def reset_chat(self):
        """Reset conversational context."""
        self.history.clear()
        self.init_provider()

    def _intercept_and_execute_tool(self, fn_name: str, fn_args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Automatic Safety Gating & Pre-State Snapshot Interceptor.
        1. Evaluates Friday safety policy (blocks destructive commands without approval token).
        2. Captures cryptographic pre-state snapshot for stateful mutations.
        3. Executes tool.
        4. Logs execution receipt status.
        """
        # 1. Friday Safety Policy Gating
        try:
            from actions.friday_policy import check_action_policy
            policy = check_action_policy(fn_name, fn_args)
            if policy.get("status") in ("GATED", "APPROVAL_REQUIRED") or policy.get("approval_required"):
                log.info("Action '%s' gated by safety policy: %s", fn_name, policy.get('reason'))
                return {
                    "ok": False,
                    "error": f"Action '{fn_name}' GATED by Safety Policy: {policy.get('reason')}. Please ask operator for explicit spoken confirmation before running.",
                    "gated": True,
                }
        except Exception as policy_err:
            log.warning("Safety policy check failed for '%s': %s. Blocking action (fail-closed).", fn_name, policy_err)
            return {
                "ok": False,
                "error": f"Action '{fn_name}' blocked: safety policy check encountered an error. Please retry or contact operator.",
                "gated": True,
            }

        # 2. Pre-state snapshot capture for file/terminal mutations
        receipt_action = None
        if fn_name in ("runTerminalCommand", "executePowerShell", "patchCodeFile", "createFile", "deleteFile"):
            try:
                from actions.friday_receipts import capture_pre_state
                receipt_action = capture_pre_state(
                    tool_name=fn_name,
                    params=fn_args,
                    target_path=fn_args.get("file_path") or fn_args.get("path")
                )
            except Exception:
                pass

        # 3. Execute tool
        res = execute_tool(fn_name, fn_args)

        # 4. Finalize receipt record
        if receipt_action:
            try:
                from actions.friday_receipts import record_receipt
                if isinstance(res, dict) and not res.get("ok", True):
                    record_receipt(receipt_action, status="FAILED", error=str(res.get("error")))
                else:
                    record_receipt(receipt_action, status="SUCCESS")
            except Exception:
                pass

        return res

    def process_message(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[str, Any], None]] = None,
    ) -> str:
        """
        Process user message and return the final response string.
        Executes any requested tools in the loop.
        """
        with self._process_lock:
            return self._process_message_impl(user_input, on_tool_call, on_tool_result)

    def _process_message_impl(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[str, Any], None]] = None,
    ) -> str:
        user_input = (user_input or "").strip()
        if not user_input:
            return ""

        from prime_traces import trace_logger
        active_trace = trace_logger.start_trace(user_input)

        # Track last tool call args for audit trace completeness
        _last_tool_args: Dict[str, Any] = {}

        def logged_tool_result(name: str, res: Any):
            args = _last_tool_args.pop(name, {})
            trace_logger.record_tool_call(active_trace, name, args, res if isinstance(res, dict) else {"result": str(res)})
            if on_tool_result:
                on_tool_result(name, res)

        _orig_on_tool_call = on_tool_call
        def _tracking_on_tool_call(name: str, args: Dict[str, Any]):
            _last_tool_args[name] = args
            if _orig_on_tool_call:
                _orig_on_tool_call(name, args)

        lower_in = user_input.lower()
        # Persona Switching Detection
        if lower_in.startswith("activate ") or "switch persona to " in lower_in or "switch to persona " in lower_in:
            target = lower_in.replace("activate ", "").replace("switch persona to ", "").replace("switch to persona ", "").replace(" mode", "").strip()
            if target in ("prime", "default", "cockpit", "normal"):
                self.deactivate_persona()
                msg = "Default Prime Neural Cockpit persona restored, Sir. All systems standard."
                if voice.tts_enabled:
                    voice.speak(msg)
                trace_logger.end_trace(active_trace, response=msg)
                return msg
            activated = self.activate_persona(target)
            if activated:
                msg = f"Specialist Persona **{activated}** activated. Ready for specialized operations, Sir."
                if voice.tts_enabled:
                    voice.speak(msg)
                trace_logger.end_trace(active_trace, response=msg)
                return msg

        if lower_in in ("reset persona", "deactivate persona", "restore prime", "default persona"):
            self.deactivate_persona()
            msg = "Specialist persona deactivated. Prime Autonomous Neural Cockpit online."
            if voice.tts_enabled:
                voice.speak(msg)
            trace_logger.end_trace(active_trace, response=msg)
            return msg

        # 0. Fast-Path Direct System Command Execution (Zero latency for hardware/app actions)
        fast_res = self._process_fast_command(user_input, _tracking_on_tool_call, logged_tool_result)
        if fast_res is not None:
            trace_logger.end_trace(active_trace, response=fast_res)
            self._record_memory_turn(user_input, fast_res)
            return fast_res

        provider = config.get_active_provider()

        # Zero-Leakage DLP Shield: Redact UAE Emirates ID, IBAN, Credit Cards, and API Secrets before cloud LLM calls
        cloud_input = user_input
        if provider != "ollama":
            try:
                from core.dlp_shield import dlp_shield
                cloud_input, dlp_detections = dlp_shield.sanitize_text(user_input)
                if dlp_detections:
                    log.info("DLP Shield redacted %d sensitive item(s) before cloud dispatch.", len(dlp_detections))
            except Exception as dlp_err:
                log.debug("DLP shield check skipped: %s", dlp_err)

        # 1. If provider is Gemini
        now = time.monotonic()
        gemini_cooldown = getattr(self, "_gemini_quota_exhausted_until", 0)
        if provider in ("gemini", "google") and self._gemini_chat is not None and now > gemini_cooldown:
            res = self._process_gemini(cloud_input, on_tool_call, logged_tool_result)
            if res != "__GEMINI_EXHAUSTED__":
                trace_logger.end_trace(active_trace, response=res)
                self._record_memory_turn(cloud_input, res)
                return res
            # Fallback to ultra-fast OpenAI-compatible provider (Groq / OpenRouter)
            if self._openai_client is None and (config.groq_api_key or config.openrouter_api_key):
                try:
                    from openai import OpenAI
                    if config.groq_api_key:
                        self._openai_client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=config.groq_api_key, timeout=6.0)
                        self._active_fallback_model = "openai/gpt-oss-120b"
                    elif config.openrouter_api_key:
                        self._openai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.openrouter_api_key, timeout=6.0)
                        self._active_fallback_model = "nvidia/nemotron-3.5-lightning:free"
                except Exception as ex:
                    log.warning("Could not init on-demand fallback client: %s", ex)

            if self._openai_client is not None:
                log.info("Gemini exhausted/timed-out. Falling back instantly to ultra-fast provider...")
                # If Gemini already executed tools before failing, include results to prevent re-execution
                failover_input = cloud_input
                prior_tools = getattr(self, '_last_gemini_tool_results', [])
                if prior_tools:
                    tool_summary = "; ".join(f"{t['tool']}→{str(t['result'])[:200]}" for t in prior_tools)
                    failover_input = f"{cloud_input}\n\n[SYSTEM: The following tools were already executed this turn. Do NOT re-execute them. Use the results directly.]\n{tool_summary}"
                    self._last_gemini_tool_results = []  # Clear after use
                res = self._process_openai_compatible(failover_input, on_tool_call, logged_tool_result)
                if res and not res.startswith("Network connection unreachable"):
                    trace_logger.end_trace(active_trace, response=res)
                    self._record_memory_turn(cloud_input, res)
                    return res

        # 2. If provider is an OpenAI-compatible provider
        if self._openai_client is not None:
            res = self._process_openai_compatible(cloud_input, on_tool_call, logged_tool_result)
            if res and not res.startswith("Network connection unreachable"):
                trace_logger.end_trace(active_trace, response=res)
                self._record_memory_turn(cloud_input, res)
                return res

        # 3. Fallback: Local rule-based command execution (100% offline!)
        res = self._process_local_fallback(user_input, _tracking_on_tool_call, logged_tool_result)
        if not res:
            res = "Offline mode active, Sir. Network unreachable. Local command engine ready."
        trace_logger.end_trace(active_trace, response=res)
        self._record_memory_turn(user_input, res)
        return res

    # Single memory extraction worker (bounded queue, no thread-per-turn spam)
    _memory_queue: queue.Queue = queue.Queue(maxsize=50)
    _memory_worker_started = False

    @classmethod
    def _start_memory_worker(cls):
        """Start the single background memory extraction worker."""
        if cls._memory_worker_started:
            return
        cls._memory_worker_started = True

        def _worker():
            while True:
                try:
                    user_text, assistant_text = cls._memory_queue.get(timeout=2.0)
                except queue.Empty:
                    continue
                try:
                    from memory.brain import auto_extract_from_turn
                    auto_extract_from_turn(user_text, assistant_text)
                except Exception:
                    pass
                finally:
                    cls._memory_queue.task_done()

        t = threading.Thread(target=_worker, daemon=True, name="prime-memory-worker")
        t.start()

    def _record_memory_turn(self, user_text: str, assistant_text: str):
        """Queue a turn for asynchronous memory extraction (bounded, single worker)."""
        if not user_text or not assistant_text or assistant_text == "__GEMINI_EXHAUSTED__":
            return
        self._start_memory_worker()
        try:
            self._memory_queue.put_nowait((user_text, assistant_text))
        except queue.Full:
            pass  # Drop oldest-unprocessed turns gracefully under load

    def _process_gemini(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]],
        on_tool_result: Optional[Callable[[str, Any], None]],
    ) -> str:
        from google.genai import types
        
        executed_tool_results = []  # Track tools executed this turn for idempotent failover

        try:
            resp = self._gemini_chat.send_message(user_input)
            function_calls = getattr(resp, "function_calls", None)

            # Path A: Direct conversational response without tools
            if not function_calls:
                final_text = resp.text or ""
                if voice.tts_enabled and final_text:
                    voice.speak(final_text)
                return final_text.strip()

            # Path B: Tool calling loop
            current_calls = function_calls
            max_iterations = 8
            for _ in range(max_iterations):
                tool_results = []
                for call in current_calls:
                    fn_name = call.name
                    fn_args = dict(call.args or {})
                    if on_tool_call:
                        on_tool_call(fn_name, fn_args)

                    # Mark-LIV Instant Acknowledgment
                    if voice.tts_enabled and fn_name in TOOL_ACKS:
                        voice.speak(TOOL_ACKS[fn_name])

                    exec_res = self._intercept_and_execute_tool(fn_name, fn_args)
                    executed_tool_results.append({"tool": fn_name, "args": fn_args, "result": exec_res})
                    if on_tool_result:
                        on_tool_result(fn_name, exec_res)

                    # Self-Correction & High-Accuracy feedback
                    if isinstance(exec_res, dict) and not exec_res.get("ok", True):
                        err_msg = exec_res.get("error", "Execution failed")
                        res_payload = {
                            "ok": False,
                            "error": err_msg,
                            "self_correction_guidance": f"Tool '{fn_name}' execution failed: {err_msg}. Review parameters, correct any invalid arguments, or call an alternative tool now to fulfill the user request."
                        }
                    else:
                        res_payload = {"result": exec_res.get("result", exec_res)}

                    try:
                        clean_payload = json.loads(json.dumps(res_payload, default=str))
                    except Exception:
                        clean_payload = {"result": str(res_payload)}

                    tool_results.append(types.Part.from_function_response(
                        name=fn_name,
                        response=clean_payload,
                    ))

                # Send tool responses turn
                resp = self._gemini_chat.send_message(tool_results)
                next_calls = getattr(resp, "function_calls", None)
                if next_calls:
                    current_calls = next_calls
                    continue
                else:
                    final_text = resp.text or ""
                    if voice.tts_enabled and final_text:
                        voice.speak(final_text)
                    return final_text.strip()

            return "Tool operations completed, Sir."
        except Exception as e:
            err_str = str(e)
            log.warning("Gemini chat error: %s. Initiating fast failover...", e)
            is_transient_or_quota = any(k in err_str for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "timed out", "timeout", "DeadlineExceeded"))
            if is_transient_or_quota:
                # Back off Gemini for 60 seconds to route directly to Groq without stalling user turns
                self._gemini_quota_exhausted_until = time.monotonic() + 60.0
                log.info("Gemini backoff activated for 60s due to transient/quota error: %s", err_str[:120])

            # Try single fast failover to gemini-3.1-flash-lite ONLY if not a service-wide 503/timeout/429
            if not is_transient_or_quota and getattr(self, "_current_gemini_model", "") != "gemini-3.1-flash-lite":
                try:
                    log.info("Failing over directly to Gemini 3.1 Flash Lite...")
                    self._gemini_chat = self._gemini_client.chats.create(
                        model="gemini-3.1-flash-lite",
                        config=types.GenerateContentConfig(
                            system_instruction=self.get_system_prompt(),
                            tools=get_gemini_tools(),
                            temperature=0.1,
                        ),
                    )
                    self._current_gemini_model = "gemini-3.1-flash-lite"
                    return self._process_gemini(user_input, on_tool_call, on_tool_result)
                except Exception as fb_err:
                    log.warning("Fast failover to Gemini 3.1 Flash Lite failed: %s", fb_err)

            # Gemini models exhausted or timed out
            log.info("Gemini exhausted/timed-out. Routing to ultra-fast provider fallback...")
            self._last_gemini_tool_results = executed_tool_results
            return "__GEMINI_EXHAUSTED__"

    def _process_openai_compatible(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]],
        on_tool_result: Optional[Callable[[str, Any], None]],
    ) -> str:
        provider = config.get_active_provider()
        model = getattr(self, "_active_fallback_model", None)
        if not model:
            if provider in ("gemini", "google"):
                model = "openai/gpt-oss-120b" if config.groq_api_key else "gpt-4o"
            else:
                model = config.get_default_model(provider)
        tools = get_openai_tools()

        user_message = {"role": "user", "content": user_input}

        # Dynamic Execution Accuracy Directive injection
        from core.intent_router import classify_intent, get_accuracy_directive
        intent = classify_intent(user_input)
        directive = get_accuracy_directive(intent)
        system_content = self.get_system_prompt()
        if directive:
            system_content += f"\n\n=========================================\nREAL-TIME EXECUTION ACCURACY DIRECTIVE ({intent})\n=========================================\n{directive}\n"

        messages = [{"role": "system", "content": system_content}] + self.history[-10:] + [user_message]

        try:
            max_iterations = 6
            for _ in range(max_iterations):
                resp = self._openai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.1,
                    timeout=6.0,
                )
                if not resp.choices:
                    return 'Provider returned empty response.'
                msg = resp.choices[0].message
                messages.append(msg)

                if not msg.tool_calls:
                    final_text = msg.content or ""
                    self.history.append(user_message)
                    self.history.append({"role": "assistant", "content": final_text})
                    if len(self.history) > 100:
                        self.history = self.history[-100:]
                    if voice.tts_enabled and final_text:
                        voice.speak(final_text)
                    return final_text

                # Execute tool calls
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        fn_args = {}

                    if on_tool_call:
                        on_tool_call(fn_name, fn_args)

                    # Mark-LIV Instant Acknowledgment
                    if voice.tts_enabled and fn_name in TOOL_ACKS:
                        voice.speak(TOOL_ACKS[fn_name])

                    exec_res = self._intercept_and_execute_tool(fn_name, fn_args)
                    if on_tool_result:
                        on_tool_result(fn_name, exec_res)

                    # Self-Correction & High-Accuracy feedback
                    if isinstance(exec_res, dict) and not exec_res.get("ok", True):
                        err_msg = exec_res.get("error", "Execution failed")
                        res_content = {
                            "ok": False,
                            "error": err_msg,
                            "self_correction_guidance": f"Tool '{fn_name}' execution failed: {err_msg}. Review parameters, correct any invalid arguments, or call an alternative tool now to fulfill the user request."
                        }
                    else:
                        res_content = exec_res

                    try:
                        content_str = json.dumps(res_content, default=str)
                    except Exception:
                        content_str = json.dumps({"result": str(res_content)})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": content_str,
                    })

            final_text = "Action completed."
            self.history.append(user_message)
            self.history.append({"role": "assistant", "content": final_text})
            if len(self.history) > 100:
                self.history = self.history[-100:]
            return final_text
        except Exception as e:
            log.warning("OpenAI-compatible provider error (%s). Falling back to local offline engine...", e)
            local_res = self._process_local_fallback(user_input, on_tool_call, on_tool_result)
            if local_res:
                return local_res
            return "Network connection unreachable, Sir. Local offline command engine ready."

    def _process_fast_command(
        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[str, Any], None]] = None,
    ) -> Optional[str]:
        """
        Sub-millisecond fast-path execution for direct hardware, window, and app operations.
        Returns a response string if handled directly, or None if the request requires LLM reasoning.
        """
        lower = user_input.lower().strip()

        def _extract_res_str(r_obj: Any, default: str = "") -> str:
            if isinstance(r_obj, dict):
                r = r_obj.get("result", default)
                if isinstance(r, dict):
                    return str(r.get("result", r))
                return str(r)
            return str(r_obj) if r_obj else default

        # 0A. Voice-First HITL Approval / Rejection Fast-Path (Trilingual: English, Hindi, UAE Arabic)
        m_approve = re.search(r"^(?:yes\s+)?(?:approve|allow|kar\s*do|haan\s*kar\s*do|permission\s*granted|grant\s*permission|mowafiq|tamam)(?:\s+(?:it|this|that|action|dot))?$", lower) or lower in ("approve", "yes approve", "approve it", "approve karo", "permission granted", "grant permission", "allow it", "kar do", "ha kar do", "yes do it", "mowafiq", "tamam", "موافق", "نعم", "اعتمد", "تمام")
        m_reject = re.search(r"^(?:reject|deny|cancel|mat\s*karo|reject\s*kar\s*do)(?:\s+(?:it|this|that|action|dot|approval))?$", lower) or lower in ("reject", "reject it", "deny", "cancel", "cancel approval", "mat karo", "reject kar do", "cancel that", "deny permission", "رفض", "الغاء", "لا")
        if m_approve or m_reject:
            from core.prime_dots import dot_engine
            pending = dot_engine.get_pending_approvals()
            if pending:
                target_dot = pending[0]
                target_id = target_dot["dot_id"]
                target_name = target_dot["name"]
                is_approval = bool(m_approve)
                ok = dot_engine.approve_dot_action(target_id, approved=is_approval)
                decision_str = "approved" if is_approval else "rejected"
                if ok:
                    msg = f"Sir, I have {decision_str} the requested action for Dot '{target_name}'. Execution has resumed." if is_approval else f"Sir, I have rejected the action for Dot '{target_name}'. The Dot will adjust its plan."
                else:
                    msg = f"Could not update approval for Dot '{target_name}'."
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg

        # 0B. Stapler Dictation Toggle Fast-Path
        if any(k in lower for k in ("toggle stapler", "start stapler", "stop stapler", "enable dictation", "disable dictation", "stapler dictation")):
            from actions.stapler_dictation import stapler_engine
            if "stop" in lower or "disable" in lower:
                stapler_engine.stop()
                msg = "Stapler system-wide dictation deactivated."
            elif "start" in lower or "enable" in lower:
                stapler_engine.start()
                msg = "Stapler system-wide dictation activated. Press Ctrl+Alt+Space anywhere on Windows to dictate."
            else:
                active = stapler_engine.toggle()
                msg = "Stapler system-wide dictation activated. Press Ctrl+Alt+Space to dictate." if active else "Stapler system-wide dictation deactivated."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 0C. Open 2D Virtual Office Floor HUD
        if any(k in lower for k in ("open office floor", "show office floor", "open office", "2d office", "office floor")):
            from core.prime_dots import ensure_office_server
            ensure_office_server(8765)
            from desktop_agent.tools_websites import open_url_in_chrome
            open_url_in_chrome("http://localhost:8765/office")
            msg = "Opening Prime AI 2D Virtual Office Floor in Google Chrome, Sir."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 1. Close Active Window / App (Generic English & Hinglish)
        if (
            re.search(r"^(?:close|exit|quit|band\s*karo|band\s*kar\s*do|hatao)\s*(?:the\s+)?(?:app|application|window|this|current|ye\s*app|is\s*app)?\s*(?:please)?$", lower)
            or re.search(r"^(?:ye\s*app|is\s*app|window|app|application)\s*(?:ko\s*)?(?:close\s*karo|close\s*kar\s*do|band\s*karo|band\s*kar\s*do|hatao)$", lower)
            or lower in ("close", "band karo", "band kar do", "close app", "app close karo", "close this", "close window", "window band karo", "ye close karo", "is app ko close karo", "isko close karo", "ye band karo")
        ):
            from desktop_agent.tools_windows import close_window
            if on_tool_call:
                on_tool_call("closeWindow", {})
            res = close_window({})
            if on_tool_result:
                on_tool_result("closeWindow", res)
            msg = res.get("result", "Closed active window.")
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 2. Close Named Application (English & Hinglish)
        m_close = (
            re.search(r"^(?:close|exit|quit|kill|terminate|shut\s*down)\s+(?:the\s+)?([a-zA-Z0-9_\-\.\s]+?)(?:\s+(?:app|application|window|please))?$", lower)
            or re.search(r"^(?:band\s+karo|band\s+kar\s+do|hatao|khatam\s+karo)\s+(?:the\s+)?([a-zA-Z0-9_\-\.\s]+?)(?:\s+(?:app|application|window))?$", lower)
            or re.search(r"^([a-zA-Z0-9_\-\.\s]+?)\s+(?:close\s+karo|close\s+kar\s+do|band\s+karo|band\s+kar\s+do|hatao|kill\s+karo)$", lower)
        )
        if m_close:
            target = m_close.group(1).strip()
            target = re.sub(r"\b(app|application|the|please|window)\b", "", target, flags=re.IGNORECASE).strip()
            if target and target not in ("this", "active", "current", "ye", "is"):
                if on_tool_call:
                    on_tool_call("closeApplication", {"name": target})
                res = self._intercept_and_execute_tool("closeApplication", {"name": target})
                if on_tool_result:
                    on_tool_result("closeApplication", res)
                msg = _extract_res_str(res, f"Closed {target}.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg
            else:
                from desktop_agent.tools_windows import close_window
                if on_tool_call:
                    on_tool_call("closeWindow", {})
                res = close_window({})
                if on_tool_result:
                    on_tool_result("closeWindow", res)
                msg = res.get("result", "Closed active window.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg

        # 3. Fast Navigation / Open website in Google Chrome or Open Application
        if lower.strip() in ("navigate", "navigate to", "go to", "browse", "browse to"):
            msg = "Where would you like to navigate, Sir? E.g., YouTube, Google, or GitHub."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        m_app = (
            re.match(r"^(?:open|launch|start|kholo|chalao|navigate\s+to|go\s+to|browse\s+to)\s+(?:the\s+)?(?:app\s+)?([a-zA-Z0-9\s\.\-_]+)$", lower)
            or re.match(r"^(?:app\s+open\s+(?:karo\s+)?)([a-zA-Z0-9\s\.\-_]+)$", lower)
            or re.match(r"^([a-zA-Z0-9\s\.\-_]+)\s+(?:open|launch|start|kholo|chalao)(?:\s+karo)?$", lower)
        )
        if m_app:
            raw_target = m_app.group(1).strip()
            target = re.sub(r'\b(app|application|karo|please|the)\b', '', raw_target, flags=re.IGNORECASE).strip() or raw_target
            from desktop_agent.tools_websites import SITE_URLS
            web_keywords = ("youtube", "google", "github", "reddit", "twitter", "instagram", "facebook", "linkedin", "chatgpt", "netflix", "gmail", "spotify", "hotstar", "amazon", "flipkart")
            if target.lower() in SITE_URLS or any(k in target.lower() for k in web_keywords) or any(target.lower().endswith(ext) for ext in (".com", ".org", ".net", ".io", ".ai", ".in", ".co", ".app")):
                if on_tool_call:
                    on_tool_call("openWebsite", {"url": target, "name": target})
                res = self._intercept_and_execute_tool("openWebsite", {"url": target, "name": target})
                if on_tool_result:
                    on_tool_result("openWebsite", res)
                msg = f"Opening {target.title()} in Google Chrome."
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg
            else:
                if on_tool_call:
                    on_tool_call("openApplication", {"name": target})
                res = self._intercept_and_execute_tool("openApplication", {"name": target})
                if on_tool_result:
                    on_tool_result("openApplication", res)
                if res.get("ok") is False:
                    msg = res.get("error", f"Failed to open {target}.")
                else:
                    msg = _extract_res_str(res, f"Opened {target.title()}.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg

        # 4. Volume / Mute
        if lower in ("volume up", "increase volume", "awaz badao", "sound badhao"):
            self._intercept_and_execute_tool("volumeUp", {"amount": 10})
            msg = "Volume increased."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg
        if lower in ("volume down", "decrease volume", "awaz kam karo", "sound kam karo"):
            self._intercept_and_execute_tool("volumeDown", {"amount": 10})
            msg = "Volume decreased."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg
        if lower in ("mute", "unmute", "mute toggle", "awaz band karo"):
            self._intercept_and_execute_tool("muteToggle", {})
            msg = "Mute toggled."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        return None

    def _process_local_fallback(

        self,
        user_input: str,
        on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]],
        on_tool_result: Optional[Callable[[str, Any], None]],
    ) -> str:
        """
        Local regex / heuristic dispatcher when no API key is provided or offline.
        Handles direct commands like 'open chrome', 'volume up', 'system info', etc.
        """
        lower = user_input.lower().strip()

        def _extract_res_str(r_obj: Any, default: str = "") -> str:
            if isinstance(r_obj, dict):
                r = r_obj.get("result", default)
                if isinstance(r, dict):
                    return str(r.get("result", r))
                return str(r)
            return str(r_obj) if r_obj else default

        # 1. System Info
        if re.search(r'\b(system info|cpu|ram|specs|diagnostics|status)\b', lower):
            if on_tool_call:
                on_tool_call("systemInfo", {})
            res = self._intercept_and_execute_tool("systemInfo", {})
            if on_tool_result:
                on_tool_result("systemInfo", res)
            out = _extract_res_str(res, "System info fetched.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2. Volume controls
        if "volume up" in lower or "increase volume" in lower:
            self._intercept_and_execute_tool("volumeUp", {"amount": 10})
            msg = "Volume increased."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg
        if "volume down" in lower or "decrease volume" in lower:
            self._intercept_and_execute_tool("volumeDown", {"amount": 10})
            msg = "Volume decreased."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg
        if re.search(r'\bmute\b', lower):
            self._intercept_and_execute_tool("muteToggle", {})
            msg = "Mute toggled."
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 2b. WhatsApp Commands (Local / Offline / Fast-path)
        m_wa_send = (
            re.search(r'(?:send\s+)?(?:a\s+)?whatsapp(?:\s+message)?\s+(?:to\s+)?([a-zA-Z0-9\s]+?)\s+(?:saying|that|bol\s+ke|likh\s+ke|:)\s*(.+)', lower)
            or re.search(r'whatsapp\s+(?:pe\s+)?([a-zA-Z0-9\s]+?)\s+(?:ko\s+)?(?:msg|message|bolo|bhejo)\s*(.+)', lower)
            or re.search(r'([a-zA-Z0-9\s]+?)\s+ko\s+whatsapp\s+(?:pe\s+)?(?:msg|message|karo|bhejo)\s*(.+)', lower)
            or re.search(r'(?:message|msg)\s+([a-zA-Z0-9\s]+?)\s+on\s+whatsapp\s+(.+)', lower)
        )
        if m_wa_send:
            rec = m_wa_send.group(1).strip()
            msg_body = m_wa_send.group(2).strip()
            rec_clean = re.sub(r'\b(to|ko|la|se|the|my|friend)\b', '', rec, flags=re.IGNORECASE).strip() or rec
            if on_tool_call:
                on_tool_call("sendWhatsAppMessage", {"recipient": rec_clean, "message": msg_body})
            res = self._intercept_and_execute_tool("sendWhatsAppMessage", {"recipient": rec_clean, "message": msg_body})
            if on_tool_result:
                on_tool_result("sendWhatsAppMessage", res)
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "WhatsApp message dispatched.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if any(k in lower for k in ("open whatsapp", "whatsapp open", "whatsapp kholo", "kholo whatsapp", "launch whatsapp")):
            m_chat = re.search(r'(?:with|of|ka|ki|ke\s+sath)\s+([a-zA-Z0-9\s]+)', lower)
            if m_chat:
                rec = m_chat.group(1).strip()
                if on_tool_call:
                    on_tool_call("openWhatsAppChat", {"recipient": rec})
                res = self._intercept_and_execute_tool("openWhatsAppChat", {"recipient": rec})
                if on_tool_result:
                    on_tool_result("openWhatsAppChat", res)
                out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, f"Opened WhatsApp chat with {rec}.")
            else:
                if on_tool_call:
                    on_tool_call("openApplication", {"name": "whatsapp"})
                res = self._intercept_and_execute_tool("openApplication", {"name": "whatsapp"})
                if on_tool_result:
                    on_tool_result("openApplication", res)
                out = "Opening WhatsApp on your desktop, Sir."
            if voice.tts_enabled:
                voice.speak(out)
            return out

        m_wa_call = (
            re.search(r'(?:call|voice\s+call|video\s+call)\s+(?:to\s+)?([a-zA-Z0-9\s]+?)\s+(?:on|via)\s+whatsapp', lower)
            or re.search(r'whatsapp\s+(?:pe\s+)?([a-zA-Z0-9\s]+?)\s+ko\s+call\s+karo', lower)
            or re.search(r'whatsapp\s+call\s+(?:to\s+)?([a-zA-Z0-9\s]+)', lower)
        )
        if m_wa_call:
            rec = m_wa_call.group(1).strip()
            ctype = "video" if "vid" in lower else "voice"
            if on_tool_call:
                on_tool_call("makeWhatsAppCall", {"recipient": rec, "call_type": ctype})
            res = self._intercept_and_execute_tool("makeWhatsAppCall", {"recipient": rec, "call_type": ctype})
            if on_tool_result:
                on_tool_result("makeWhatsAppCall", res)
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, f"Initiating WhatsApp call to {rec}.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2c. WhatsApp Focus Mode & Unread Fast-Path
        if any(k in lower for k in ("unread whatsapp", "whatsapp unread", "check whatsapp", "any new messages", "check unread")):
            res = self._intercept_and_execute_tool("checkWhatsAppUnread", {})
            if isinstance(res, dict) and res.get("has_unread"):
                out = f"Sir, you have {res.get('unread_count')} unread WhatsApp messages."
            else:
                out = "You have no unread WhatsApp messages, Sir."
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if "focus mode" in lower:
            enable = not any(k in lower for k in ("off", "disable", "band", "deactivate"))
            res = self._intercept_and_execute_tool("setWhatsAppFocusMode", {"enabled": enable})
            out = f"WhatsApp focus mode is now {'enabled' if enable else 'disabled'}, Sir."
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2d. Second Brain / Obsidian Notes Fast-Path
        m_notes = (
            re.search(r'(?:search\s+notes\s+(?:for\s+)?|notes\s+(?:me\s+)?search\s+(?:karo\s+)?|find\s+in\s+notes\s+)(.+)', lower)
            or re.search(r'(?:check\s+obsidian\s+(?:for\s+)?|obsidian\s+search\s+)(.+)', lower)
        )
        if m_notes:
            q = m_notes.group(1).strip()
            if on_tool_call:
                on_tool_call("queryObsidianKnowledgeBase", {"query": q})
            res = self._intercept_and_execute_tool("queryObsidianKnowledgeBase", {"query": q, "top_k": 3})
            if on_tool_result:
                on_tool_result("queryObsidianKnowledgeBase", res)
            count = res.get("count", 0) if isinstance(res, dict) else 0
            if count > 0:
                first_match = res.get("results", [{}])[0]
                out = f"Found {count} matching notes in your Second Brain. Most relevant: {first_match.get('heading', 'Note')} from {first_match.get('path')}."
            else:
                out = f"I could not find any notes matching '{q}' in your Obsidian Vault, Sir."
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2e. Autonomous Developer & Testing Fast-Path
        if lower in ("run tests", "run test suite", "test run karo", "run unit tests", "execute tests"):
            if on_tool_call:
                on_tool_call("runUnitTests", {})
            res = self._intercept_and_execute_tool("runUnitTests", {})
            if on_tool_result:
                on_tool_result("runUnitTests", res)
            passed = res.get("ok", False)
            out = "All unit tests passed successfully, Sir!" if passed else "Some unit tests failed, Sir. Reviewing output."
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if lower in ("git status", "check git", "git status check karo"):
            res = self._intercept_and_execute_tool("gitAutomate", {"action": "status"})
            out = f"Git status: {res.get('status', 'Clean')}"
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if lower.startswith("self heal") or lower.startswith("auto fix"):
            m_cmd = re.search(r'(?:self\s+heal|auto\s+fix)\s+(.+)', lower)
            cmd_to_heal = m_cmd.group(1).strip() if m_cmd else "pytest"
            if on_tool_call:
                on_tool_call("runAutonomousCodeRepair", {"command": cmd_to_heal})
            res = self._intercept_and_execute_tool("runAutonomousCodeRepair", {"command": cmd_to_heal, "max_attempts": 3})
            if on_tool_result:
                on_tool_result("runAutonomousCodeRepair", res)
            out = res.get("message", "Self-healing cycle complete.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2f. Visual UI Grounding Fast-Path
        m_click = (
            re.match(r"(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+)", lower)
            or re.match(r"screen\s+pe\s+(.+?)\s+(?:pe\s+)?(?:click|dabao)\s*karo", lower)
        )
        if m_click and not any(k in lower for k in ("mouse", "right click", "double click", "link", "here", "enter", "space", "esc", "tab", "whatsapp", "app", "window")):
            target_elem = m_click.group(1).strip()
            if target_elem not in ("start", "enter", "space", "esc", "tab", "mute", "volume", "music"):
                if on_tool_call:
                    on_tool_call("locateAndClickUI", {"element": target_elem})
                res = self._intercept_and_execute_tool("locateAndClickUI", {"element": target_elem})
                if on_tool_result:
                    on_tool_result("locateAndClickUI", res)
                out = res.get("message") if isinstance(res, dict) and res.get("ok") else (res.get("error") if isinstance(res, dict) else str(res))
                if voice.tts_enabled:
                    voice.speak(out)
                return out

        # 2g. Wireless Android Fast-Path (Ultron A Voice with Hands)
        if any(k in lower for k in ("phone battery", "mobile battery", "battery on phone", "phone ki battery")):
            res = self._intercept_and_execute_tool("androidBattery", {})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "Fetched phone battery.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if any(k in lower for k in ("unlock phone", "phone unlock", "phone ko unlock karo", "wake phone")):
            res = self._intercept_and_execute_tool("androidUnlock", {})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "Phone unlocked.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if any(k in lower for k in ("lock phone", "phone lock", "phone screen off", "phone ko lock karo")):
            res = self._intercept_and_execute_tool("androidLock", {})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "Phone locked.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        m_phone_app = (
            re.search(r'(?:open|launch)\s+([a-zA-Z0-9\s]+?)\s+(?:on|in)\s+(?:the\s+)?(?:phone|mobile)', lower)
            or re.search(r'phone\s+(?:pe|par|me|mein)\s+([a-zA-Z0-9\s]+?)\s+(?:open|kholo|chalao)', lower)
            or re.search(r'(?:phone|mobile)\s+(?:open|launch)\s+([a-zA-Z0-9\s]+)', lower)
        )
        if m_phone_app:
            app_target = m_phone_app.group(1).strip()
            res = self._intercept_and_execute_tool("androidOpenApp", {"app_name": app_target})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, f"Opening {app_target} on phone.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if any(k in lower for k in ("phone notification", "phone notifications", "check phone notifications", "mobile notifications")):
            res = self._intercept_and_execute_tool("androidNotifications", {})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "Checked phone notifications.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        m_phone_media = re.search(r'phone\s+(?:media\s+)?(play|pause|next|previous|stop|volume_up|volume_down)', lower)
        if m_phone_media:
            action = m_phone_media.group(1).strip()
            res = self._intercept_and_execute_tool("androidMediaControl", {"action": action})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, f"Phone media: {action}.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        if any(k in lower for k in ("connected phones", "phone devices", "list phones", "adb devices")):
            res = self._intercept_and_execute_tool("androidListDevices", {})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, "Listed devices.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 2h. Stark Intercom Audio DSP Filter Fast-Path
        if any(k in lower for k in ("stark filter", "intercom filter", "iron man voice", "radio filter", "dsp filter")):
            enable = not any(k in lower for k in ("off", "disable", "band", "deactivate"))
            res = self._intercept_and_execute_tool("toggleStarkAudioFilter", {"enabled": enable})
            out = res.get("message") if isinstance(res, dict) else _extract_res_str(res, f"Stark audio filter {'enabled' if enable else 'disabled'}.")
            if voice.tts_enabled:
                voice.speak(out)
            return out

        # 3. Open applications or websites
        m_app = (
            re.match(r"(?:open|launch|start|kholo|chalao)\s+(?:the\s+)?(?:app\s+)?([a-zA-Z0-9\s\.\-_]+)", lower)
            or re.match(r"(?:app\s+open\s+(?:karo\s+)?)([a-zA-Z0-9\s\.\-_]+)", lower)
            or re.match(r"([a-zA-Z0-9\s\.\-_]+)\s+(?:open|launch|start|kholo|chalao)(?:\s+karo)?", lower)
        )
        if m_app:
            raw_target = m_app.group(1).strip()
            # Clean filler words
            target = re.sub(r'\b(app|application|karo|please|the)\b', '', raw_target, flags=re.IGNORECASE).strip()
            if not target:
                target = raw_target

            # Check if it's a website or app
            from desktop_agent.tools_websites import SITE_URLS
            web_keywords = ("youtube", "google", "github", "reddit", "twitter", "instagram", "facebook", "linkedin", "chatgpt", "netflix", "gmail", "spotify", "hotstar", "amazon", "flipkart")
            if target.lower() in SITE_URLS or any(k in target.lower() for k in web_keywords) or any(target.lower().endswith(ext) for ext in (".com", ".org", ".net", ".io", ".ai", ".in", ".co", ".app")):
                self._intercept_and_execute_tool("openWebsite", {"url": target, "name": target})
                msg = f"Opening {target} in Google Chrome."
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg
            else:
                res = self._intercept_and_execute_tool("openApplication", {"name": target})
                if res.get("ok") is False:
                    msg = res.get("error", f"Failed to open {target}.")
                else:
                    msg = _extract_res_str(res, f"Opened {target}.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg

        # 4a. Close active window / generic app close intent (English & Hinglish)
        if (
            re.search(r"^(?:close|exit|quit|band\s*karo|band\s*kar\s*do|hatao)\s*(?:the\s+)?(?:app|application|window|this|current|ye\s*app|is\s*app)?\s*(?:please)?$", lower)
            or re.search(r"^(?:ye\s*app|is\s*app|window|app|application)\s*(?:ko\s*)?(?:close\s*karo|close\s*kar\s*do|band\s*karo|band\s*kar\s*do|hatao)$", lower)
            or lower in ("close", "band karo", "band kar do", "close app", "app close karo", "close this", "close window", "window band karo", "ye close karo", "is app ko close karo")
        ):
            from desktop_agent.tools_windows import close_window
            try:
                res = close_window({})
                msg = res.get("result", "Closed active window.")
            except Exception:
                res = self._intercept_and_execute_tool("closeApplication", {"name": "active"})
                msg = _extract_res_str(res, "Closed active application.")
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 4b. Close named application (supports prefix & suffix phrasing in English & Hinglish)
        m_close = (
            re.search(r"^(?:close|exit|quit|kill|terminate|shut\s*down)\s+(?:the\s+)?([a-zA-Z0-9_\-\.\s]+?)(?:\s+(?:app|application|window|please))?$", lower)
            or re.search(r"^(?:band\s+karo|band\s+kar\s+do|hatao|khatam\s+karo)\s+(?:the\s+)?([a-zA-Z0-9_\-\.\s]+?)(?:\s+(?:app|application|window))?$", lower)
            or re.search(r"^([a-zA-Z0-9_\-\.\s]+?)\s+(?:close\s+karo|close\s+kar\s+do|band\s+karo|band\s+kar\s+do|hatao|kill\s+karo)$", lower)
            or re.search(r"\b(?:close|kill|exit)\s+([a-zA-Z0-9_\-\.]+)\b", lower)
        )
        if m_close:
            target = m_close.group(1).strip()
            # Clean any remaining filler words
            target = re.sub(r"\b(app|application|the|please|window)\b", "", target, flags=re.IGNORECASE).strip()
            if target:
                res = self._intercept_and_execute_tool("closeApplication", {"name": target})
                if res.get("ok") is False:
                    msg = res.get("error", f"Failed to close {target}.")
                else:
                    msg = _extract_res_str(res, f"Closed {target}.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg
            else:
                from desktop_agent.tools_windows import close_window
                res = close_window({})
                msg = res.get("result", "Closed active window.")
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg


        # 5. YouTube Search (only on explicit search intent)
        if "search youtube for" in lower or "youtube search" in lower or lower.startswith("search youtube"):
            q = re.sub(r"(search youtube for|youtube search|search youtube|play|on youtube)", "", lower).strip()
            if q:
                self._intercept_and_execute_tool("searchYouTube", {"query": q})
                msg = f"Searching YouTube for {q}."
                if voice.tts_enabled:
                    voice.speak(msg)
                return msg

        # 6. Screenshot
        if "screenshot" in lower:
            res = self._intercept_and_execute_tool("saveScreenshot", {})
            msg = _extract_res_str(res, "Screenshot captured.")
            if voice.tts_enabled:
                voice.speak(msg)
            return msg

        # 7. Local Code Generation Heuristic (works offline or during rate limits)
        if re.search(r'\b(code|python|script|program|write code|make script)\b', lower):
            m_fn = re.search(r"(\w+\.py)", user_input)
            filename = m_fn.group(1) if m_fn else "prime_generated_script.py"
            safe_input = repr(user_input)
            code_content = (
                f'"""\nGenerated by Prime Autonomous Code Engine\nTask Request: {user_input}\n"""\n\n'
                f'import sys\nimport os\n\n'
                f'def run():\n'
                f'    print("Executing: " + {safe_input})\n'
                f'    # Prime autonomous script execution logic\n\n'
                f'if __name__ == "__main__":\n'
                f'    run()\n'
            )
            res = self._intercept_and_execute_tool("createPythonFile", {"filename": filename, "code": code_content})
            if res.get("ok") is False:
                out_msg = res.get("error", "Failed to create python file.")
            else:
                out_msg = res.get("result", {}).get("result", f"Generated {filename} on Desktop.")
            if voice.tts_enabled:
                voice.speak(out_msg)
            return f"✓ {out_msg}\n```python\n{code_content}\n```"

        # 8. Handling when API keys are configured vs absent
        if config.gemini_api_key or config.groq_api_key or config.openai_api_key:
            msg = (
                f"I received: '{user_input}'.\n\n"
                "The cloud AI model experienced a brief rate-limit or network cooldown.\n"
                "Please retry in a few moments, or use direct commands like:\n"
                "  - 'open <app>', 'system info', 'volume up/down', 'screenshot', 'code <task>'."
            )
            if voice.tts_enabled:
                voice.speak("AI model temporarily busy. Please repeat your instruction in a few moments.")
            return msg

        # Only shown if user truly has NO API keys in .env
        advisory = (
            "I heard: '" + user_input + "'.\n\n"
            "To unlock full conversational AI reasoning and multi-step tool execution,\n"
            "please configure your Gemini or Groq API key:\n"
            "  1. Run `/key <your_gemini_or_groq_key>` here in the terminal, or\n"
            "  2. Add GEMINI_API_KEY=... in your .env file.\n\n"
            "Basic offline commands currently work: 'open <app>', 'system info', 'volume up/down', 'screenshot'."
        )
        if voice.tts_enabled:
            voice.speak("Please configure an API key for full conversational capabilities.")
        return advisory

    # Method alias for external compatibility
    process_input = process_message


agent = AIAgent()
