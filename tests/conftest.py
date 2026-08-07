import pytest

from assessor.config import Settings

# Shared LLM-off Settings for tests that reach assemble.build/purpose.extract/
# probes.detect but don't exercise the LLM path itself. `llm_provider="none"` is
# pinned explicitly rather than left to the field default: Settings reads the
# real environment and .env (config.py), so a stray .env or an exported
# LLM_PROVIDER/LLM_BASE_URL/LLM_MODEL would otherwise flip these tests LLM-on
# and let them attempt real HTTP calls.
_S = Settings(assessor_api_token="x", llm_provider="none")


@pytest.fixture
def anyio_backend():
    return "asyncio"
