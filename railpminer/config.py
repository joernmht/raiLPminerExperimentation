"""Central configuration for raiLPminer.

No LLM API calls happen at import time: the model registry stores *factories*,
not live client objects.  A factory is only invoked when :func:`get_model` is
called for that key.

Open-source model focus
-----------------------
All generation models are open-weight models served through an
OpenAI-compatible aggregator (OpenRouter by default).  This keeps the
pydantic-ai ``Agent`` code unchanged while making every result reproducible
with publicly available weights.  Set ``OPENROUTER_API_KEY`` (or point
``OPENROUTER_BASE_URL`` at a local vLLM/Ollama OpenAI endpoint) to run them.
"""

import os
from pathlib import Path

# Directory layout
PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent
TABLES_DIR = PROJECT_ROOT / "tables"
FIGS_DIR = PROJECT_ROOT / "figs"
INPUTS_DIR = PROJECT_ROOT / "inputs"
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"

DEFAULT_CSV = "experiment_results_metrics_corrected_selected.csv"

# OpenAI-compatible aggregator endpoint.  Override OPENROUTER_BASE_URL to use a
# local server (vLLM/Ollama) hosting the same open-weight models.
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)


# ---------------------------------------------------------------------------
# Open-weight model registry
#
# Keys are stable identifiers used throughout the experiment grid; values are
# the OpenRouter slugs of the corresponding open-weight checkpoints.  Four
# distinct model families are used so that structural-diversity differences
# cannot be attributed to a single lineage.
# ---------------------------------------------------------------------------

OPEN_MODEL_SLUGS = {
    # key                # OpenRouter slug (open-weight checkpoint)
    "llama_3_3_70b":      "meta-llama/llama-3.3-70b-instruct",
    "qwen_2_5_72b":       "qwen/qwen-2.5-72b-instruct",
    "deepseek_v3":        "deepseek/deepseek-chat",
    "mistral_small_3":    "mistralai/mistral-small-3.1-24b-instruct",
}

# Models that do not honour a sampling ``temperature`` argument.  Recorded
# explicitly instead of silently rewriting temperature values afterwards
# (see utils.data.annotate_temperature_support).
MODELS_WITHOUT_TEMPERATURE = set()


def _make_open_model(slug):
    """Build an OpenAI-compatible client for an open-weight model.

    The client talks to the aggregator defined by ``OPENROUTER_BASE_URL``
    using ``OPENROUTER_API_KEY``.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY", "not-set"),
    )
    return OpenAIChatModel(slug, provider=provider)


# Factories, created lazily so importing this module never touches the network.
MODEL_REGISTRY = {
    key: (lambda s=slug: _make_open_model(s))
    for key, slug in OPEN_MODEL_SLUGS.items()
}

# Cache so each model is built at most once per session.
_model_cache = {}


def register_model(name, factory):
    """Register an extra model factory (e.g. a local stub for offline runs)."""
    MODEL_REGISTRY[name] = factory
    _model_cache.pop(name, None)


def get_model(name):
    """Return a model instance by name, building it lazily on first use.

    Args:
        name: Key in :data:`MODEL_REGISTRY`.  If it is already a model
              object (not a string) it is returned unchanged, which lets
              callers inject a stub/test model directly.
    """
    if not isinstance(name, str):
        return name
    if name not in _model_cache:
        factory = MODEL_REGISTRY.get(name)
        if factory is None:
            raise KeyError(
                f"Unknown model '{name}'. "
                f"Available: {sorted(MODEL_REGISTRY)}"
            )
        _model_cache[name] = factory()
    return _model_cache[name]


# ---------------------------------------------------------------------------
# Problem-description registry
#
# Inputs are now *problem descriptions* (operational setting, objective and
# constraints to consider), NOT paper abstracts/introductions.  The legacy
# ``register_paper`` / ``get_paper`` names are kept as thin aliases so older
# notebooks keep working.
# ---------------------------------------------------------------------------

PROBLEM_REGISTRY = {}


def register_problem(name, content):
    """Register a problem-description text so it can be looked up by name."""
    PROBLEM_REGISTRY[name] = content


def get_problem(name):
    """Return problem-description content by name.

    Args:
        name: Key in :data:`PROBLEM_REGISTRY`.  Non-string values are
              returned unchanged.
    """
    if not isinstance(name, str):
        return name
    content = PROBLEM_REGISTRY.get(name)
    if content is None:
        raise KeyError(
            f"Unknown problem '{name}'. "
            f"Registered: {sorted(PROBLEM_REGISTRY)}. "
            f"Load descriptions via utils.io.process_inputfiles() first."
        )
    return content


# Backwards-compatible aliases (old code referred to "papers").
register_paper = register_problem
get_paper = get_problem
PAPER_REGISTRY = PROBLEM_REGISTRY
