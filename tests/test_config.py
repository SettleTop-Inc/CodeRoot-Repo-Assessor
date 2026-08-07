import pytest
from assessor.config import Settings, ConfigError


def test_auth_fails_closed_when_neither_token_nor_optout_is_set():
    with pytest.raises(ConfigError, match="ASSESSOR_API_TOKEN"):
        Settings(assessor_api_token=None, assessor_allow_anonymous=False)


def test_explicit_anonymous_optout_is_accepted():
    s = Settings(assessor_api_token=None, assessor_allow_anonymous=True)
    assert s.assessor_allow_anonymous is True


def test_token_alone_is_accepted():
    s = Settings(assessor_api_token="secret", assessor_allow_anonymous=False)
    assert s.assessor_api_token == "secret"


def test_default_bind_is_loopback_not_all_interfaces():
    s = Settings(assessor_api_token="secret")
    assert s.assessor_bind_addr == "127.0.0.1"


def test_github_token_list_splits_and_strips():
    s = Settings(assessor_api_token="x", github_tokens=" a , b ,, c ")
    assert s.github_token_list == ["a", "b", "c"]


def test_llm_defaults_to_off():
    s = Settings(assessor_api_token="x")
    assert s.llm_provider == "none"
