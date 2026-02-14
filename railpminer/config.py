"""Central configuration for raiLPminer. No LLM API calls at import time."""

import os
from pathlib import Path

# Directory layout
PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent
TABLES_DIR = PROJECT_ROOT / "tables"
FIGS_DIR = PROJECT_ROOT / "figs"

DEFAULT_CSV = "experiment_results_metrics_corrected_selected.csv"


# ---------------------------------------------------------------------------
# Lazy model registry — factories, not live objects.  Only instantiated when
# get_model() is called, so importing this module never hits the network.
# ---------------------------------------------------------------------------

def _make_deepseek_v3():
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.deepseek import DeepSeekProvider
    return OpenAIChatModel(
        'deepseek-chat',
        provider=DeepSeekProvider(api_key=os.getenv("DEEPSEEK_API_KEY")),
    )


def _make_deepseek_r1():
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.deepseek import DeepSeekProvider
    return OpenAIChatModel(
        'deepseek-reasoner',
        provider=DeepSeekProvider(api_key=os.getenv("DEEPSEEK_API_KEY")),
    )


def _make_gemini_flash():
    from pydantic_ai.models.google import GoogleModel
    return GoogleModel("gemini-2.5-flash-preview-04-17", provider='google-gla')


def _make_gemini_pro():
    from pydantic_ai.models.google import GoogleModel
    return GoogleModel('gemini-2.5-pro-preview-05-06', provider='google-gla')


def _make_openai41mini():
    from pydantic_ai.models.openai import OpenAIChatModel
    return OpenAIChatModel('gpt-4.1-mini')


def _make_openai_o4_mini():
    from pydantic_ai.models.openai import OpenAIChatModel
    return OpenAIChatModel('o4-mini')


MODEL_REGISTRY = {
    "deepseek_v3": _make_deepseek_v3,
    "deepseek_r1": _make_deepseek_r1,
    "gemini_flash": _make_gemini_flash,
    "gemini_pro": _make_gemini_pro,
    "openai41mini": _make_openai41mini,
    "openai_o4_mini": _make_openai_o4_mini,
}

# Cache so we only create each model once per session
_model_cache = {}


def get_model(name):
    """Get a model instance by name. Creates it lazily on first call.

    Args:
        name: Key in MODEL_REGISTRY (e.g. ``"openai_o4_mini"``).
              If it's already a model object (not a string), it's returned as-is.

    Returns:
        The model object.
    """
    if not isinstance(name, str):
        return name
    if name not in _model_cache:
        factory = MODEL_REGISTRY.get(name)
        if factory is None:
            raise KeyError(
                f"Unknown model '{name}'. "
                f"Available: {list(MODEL_REGISTRY.keys())}"
            )
        _model_cache[name] = factory()
    return _model_cache[name]


# ---------------------------------------------------------------------------
# Paper registry — populated at runtime via load_papers()
# ---------------------------------------------------------------------------

PAPER_REGISTRY = {}


def register_paper(name, content):
    """Register paper content so it can be looked up by name."""
    PAPER_REGISTRY[name] = content


def get_paper(name):
    """Get paper content by name.

    Args:
        name: Key in PAPER_REGISTRY (e.g. ``"Paper_1"``).
              If it's not a string, it's returned as-is.
    """
    if not isinstance(name, str):
        return name
    content = PAPER_REGISTRY.get(name)
    if content is None:
        raise KeyError(
            f"Unknown paper '{name}'. "
            f"Registered: {list(PAPER_REGISTRY.keys())}. "
            f"Use register_paper() or load papers via utils.io.process_inputfiles() first."
        )
    return content
