"""
Clasificador determinista de complejidad. Funciones puras, sin I/O.
Acepta la forma Anthropic (/v1/messages) y OpenAI (/v1/chat/completions) y también
texto libre de una tarea (broker). Cada aporte al puntaje deja un `reason` legible.
"""
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.models.smart import TIER_ORDER, tier_index

DEFAULT_THRESHOLDS = {"simple": 25, "standard": 50, "complex": 75}
BASE_SCORE = 10
CHARS_PER_TOKEN = 4

_FENCE_RE = re.compile(r"```")
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)?[\w.-]+(?:[\\/][\w.-]+)+\.\w{1,5}")

INTENT_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("debug", re.compile(r"traceback|stack ?trace|exception|error:|\bfalla\b|no funciona|se rompe|\bbug\b|causa ra[ií]z|root cause|diagnos", re.I)),
    ("plan", re.compile(r"\bplan(?:ea|ifica)?\b|dise[ñn]|arquitect|architect|propuesta|approach|trade-?off", re.I)),
    ("translate", re.compile(r"traduc|translat", re.I)),
    ("summarize", re.compile(r"resum|summar|tl;?dr", re.I)),
    ("code_edit", re.compile(r"implementa|agrega|a[ñn]ade|refactor|\bfix\b|corrige|cambia|rename|renombr|migra|crea (?:un|el|la) (?:endpoint|servicio|componente|clase)", re.I)),
    ("test", re.compile(r"\btests?\b(?![\\/])|\bspecs?\b|prueba unitaria|coverage|cobertura", re.I)),
    ("review", re.compile(r"\breview\b|revis[aá]|audit", re.I)),
    ("explain", re.compile(r"explica|qu[eé] hace|what does|how does|por qu[eé]|\bwhy\b", re.I)),
)
INTENT_SCORE = {
    "chat": -10, "translate": -10, "summarize": -10, "explain": 0,
    "test": 10, "review": 10, "code_edit": 15, "debug": 20, "plan": 20, "agentic_loop": 0,
}
REASONING_INTENTS = ("debug", "plan", "code_edit", "review", "test")
TASK_BASE_SCORE = 25  # una tarea delegada nunca es menos que "simple" por defecto
EXTRA_INTENT_POINTS = 5
EXTRA_INTENT_CAP = 15
MODEL_HINT_SCORE = {"haiku": -15, "sonnet": 5, "opus": 25, "unknown": 0}
GREETING_RE = re.compile(r"^\s*(hola|hi|hello|hey|gracias|thanks|ok|listo|buenas)\b", re.I)


@dataclass(frozen=True)
class RequestSignals:
    prompt_tokens: int = 0
    n_messages: int = 0
    n_tools: int = 0
    has_tool_results: bool = False
    has_images: bool = False
    last_user_tokens: int = 0
    code_fence_count: int = 0
    file_path_refs: int = 0
    intent: str = "chat"
    model_hint: str = "unknown"
    max_tokens: int = 0
    thinking_requested: bool = False
    surface: str = "messages"


@dataclass(frozen=True)
class Classification:
    tier: str
    score: int
    intent: str
    reasons: tuple[str, ...]
    signals: RequestSignals
    source: str = "heuristic"  # heuristic | header

    def to_dict(self) -> dict:
        return {
            "tier": self.tier, "score": self.score, "intent": self.intent,
            "reasons": list(self.reasons), "source": self.source, "signals": asdict(self.signals),
        }


# ── extracción de señales ────────────────────────────────────────────────────

def _text_of_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _block_types(content) -> set[str]:
    if not isinstance(content, list):
        return set()
    return {b.get("type", "") for b in content if isinstance(b, dict)}


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            text = _text_of_content(msg.get("content"))
            if text:
                return text
    return ""


def _last_has_tool_results(messages: list[dict]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") == "tool":
        return True
    return "tool_result" in _block_types(last.get("content"))


def _has_images(messages: list[dict]) -> bool:
    for msg in messages:
        types = _block_types(msg.get("content"))
        if "image" in types or "image_url" in types:
            return True
    return False


def model_hint_of(model: str) -> str:
    lowered = (model or "").lower()
    for hint in ("haiku", "sonnet", "opus"):
        if hint in lowered:
            return hint
    return "unknown"


def detect_intent(text: str, n_tools: int = 0, has_tool_results: bool = False) -> str:
    if has_tool_results and n_tools > 0:
        return "agentic_loop"
    for name, pattern in INTENT_RULES:
        if pattern.search(text or ""):
            return name
    return "chat"


def extra_intent_hits(text: str) -> int:
    """Intenciones de razonamiento presentes además de la principal."""
    hits = sum(1 for name, pattern in INTENT_RULES if name in REASONING_INTENTS and pattern.search(text or ""))
    return max(0, hits - 1)


def _thinking_requested(body: dict) -> bool:
    if body.get("thinking"):
        return True
    return str(body.get("reasoning_effort", "")).lower() in ("high", "xhigh", "max")


def extract_signals(body: dict, prompt_tokens: int, surface: str = "messages") -> RequestSignals:
    messages = body.get("messages") or []
    tools = body.get("tools") or body.get("functions") or []
    last_text = _last_user_text(messages)
    has_results = _last_has_tool_results(messages)
    return RequestSignals(
        prompt_tokens=int(prompt_tokens or 0),
        n_messages=len(messages),
        n_tools=len(tools),
        has_tool_results=has_results,
        has_images=_has_images(messages),
        last_user_tokens=len(last_text) // CHARS_PER_TOKEN,
        code_fence_count=len(_FENCE_RE.findall(last_text)) // 2,
        file_path_refs=len(_PATH_RE.findall(last_text)),
        intent=detect_intent(last_text, len(tools), has_results),
        model_hint=model_hint_of(body.get("model", "")),
        max_tokens=int(body.get("max_tokens") or 0),
        thinking_requested=_thinking_requested(body),
        surface=surface,
    )


def signals_from_text(task: str, files: Optional[list[str]] = None, surface: str = "task") -> RequestSignals:
    task = task or ""
    return RequestSignals(
        prompt_tokens=len(task) // CHARS_PER_TOKEN,
        n_messages=1,
        last_user_tokens=len(task) // CHARS_PER_TOKEN,
        code_fence_count=len(_FENCE_RE.findall(task)) // 2,
        file_path_refs=len(_PATH_RE.findall(task)) + len(files or []),
        intent=detect_intent(task),
        surface=surface,
    )


# ── puntaje y tier ───────────────────────────────────────────────────────────

def _token_points(tokens: int) -> int:
    if tokens < 2_000:
        return 0
    if tokens < 8_000:
        return 5
    if tokens < 32_000:
        return 10
    if tokens < 100_000:
        return 20
    return 30


def _tool_points(n_tools: int) -> int:
    if n_tools <= 0:
        return 0
    return 5 if n_tools <= 10 else 10


def score_signals(s: RequestSignals, last_text: str = "") -> tuple[int, tuple[str, ...]]:
    base = TASK_BASE_SCORE if s.surface == "task" else BASE_SCORE
    contributions: list[tuple[str, int]] = [("base", base)]
    extra = min(EXTRA_INTENT_CAP, EXTRA_INTENT_POINTS * extra_intent_hits(last_text))
    if extra:
        contributions.append(("multi_intent", extra))
    contributions.append((f"model:{s.model_hint}", MODEL_HINT_SCORE.get(s.model_hint, 0)))
    contributions.append((f"tokens:{s.prompt_tokens}", _token_points(s.prompt_tokens)))
    contributions.append((f"tools:{s.n_tools}", _tool_points(s.n_tools)))
    if s.has_tool_results:
        contributions.append(("tool_results", 15))
    contributions.append((f"intent:{s.intent}", INTENT_SCORE.get(s.intent, 0)))
    if s.code_fence_count >= 2:
        contributions.append(("code_fences", 5))
    if s.file_path_refs >= 3:
        contributions.append(("multi_file", 5))
    if s.has_images:
        contributions.append(("images", 5))
    if s.thinking_requested:
        contributions.append(("thinking", 20))
    if s.max_tokens >= 8_000:
        contributions.append(("long_output", 5))
    if s.last_user_tokens < 10 and s.n_tools == 0 and GREETING_RE.search(last_text or ""):
        contributions.append(("greeting", -15))
    total = max(0, min(100, sum(points for _, points in contributions)))
    reasons = tuple(f"{name}:{points:+d}" for name, points in contributions if points)
    return total, reasons


def tier_from_score(score: int, thresholds: Optional[dict] = None) -> str:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if score < th["simple"]:
        return "trivial"
    if score < th["standard"]:
        return "simple"
    if score < th["complex"]:
        return "standard"
    return "complex"


def _apply_floors(tier: str, s: RequestSignals, reasons: list[str]) -> str:
    floor = ""
    if s.thinking_requested:
        floor = "complex"
    elif s.model_hint == "opus":
        floor = "standard"
    if floor and tier_index(tier) < tier_index(floor):
        reasons.append(f"floor:{floor}")
        return floor
    return tier


def classify_signals(s: RequestSignals, thresholds: Optional[dict] = None, last_text: str = "") -> Classification:
    score, reasons = score_signals(s, last_text)
    reasons_list = list(reasons)
    tier = _apply_floors(tier_from_score(score, thresholds), s, reasons_list)
    reasons_list.append(f"tier:{tier}({score})")
    return Classification(tier=tier, score=score, intent=s.intent, reasons=tuple(reasons_list), signals=s)


def classify_request(body: dict, prompt_tokens: int, thresholds: Optional[dict] = None, surface: str = "messages") -> Classification:
    signals = extract_signals(body, prompt_tokens, surface)
    return classify_signals(signals, thresholds, _last_user_text(body.get("messages") or []))


def classify_task(task: str, files: Optional[list[str]] = None, thresholds: Optional[dict] = None) -> Classification:
    signals = signals_from_text(task, files)
    return classify_signals(signals, thresholds, task)


def with_header_override(cls: Classification, header_tier: Optional[str]) -> Classification:
    if header_tier not in TIER_ORDER:
        return cls
    return Classification(
        tier=header_tier, score=cls.score, intent=cls.intent,
        reasons=cls.reasons + (f"header:{header_tier}",), signals=cls.signals, source="header",
    )


def conversation_key(body: dict) -> str:
    """Clave estable de una conversación para stickiness: system + primer mensaje user."""
    import hashlib
    system = body.get("system")
    system_text = system if isinstance(system, str) else _text_of_content(system) if system else ""
    first_user = ""
    for msg in body.get("messages") or []:
        if msg.get("role") == "user":
            first_user = _text_of_content(msg.get("content"))
            break
    return hashlib.sha1((system_text[:512] + "\n" + first_user[:512]).encode("utf-8", "ignore")).hexdigest()
