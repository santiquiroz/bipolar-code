"""Contrato de delegación: campos nuevos con defaults neutros y límites."""
import pytest
from pydantic import ValidationError

from app.models.delegate import Attempt, Job, JobRequest
from app.models.smart import DelegationConfig


def test_job_request_defaults_are_neutral():
    req = JobRequest(task="x")
    assert req.verify == [] and req.review is None and req.max_revisions == 1


def test_job_request_limits():
    with pytest.raises(ValidationError):
        JobRequest(task="x", verify=["pytest"] * 11)
    with pytest.raises(ValidationError):
        JobRequest(task="x", verify=["a" * 501])
    with pytest.raises(ValidationError):
        JobRequest(task="x", max_revisions=4)


def test_delegation_config_defaults():
    cfg = DelegationConfig()
    assert cfg.allow_request_verify is False and cfg.review_default is True
    assert cfg.verify_timeout_s == 600 and cfg.review_timeout_s == 900


def test_attempt_and_job_defaults():
    attempt = Attempt(agent_id="claude", started_at="t")
    assert attempt.kind == "work" and attempt.detail == {}
    job = Job(id="j", created_at="t")
    assert job.verification_status == "n/a" and job.review_status == "n/a" and job.escalations == 0
