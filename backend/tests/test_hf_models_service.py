from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import hf_models_service


@pytest.fixture(autouse=True)
def reset_download_state():
    hf_models_service._downloads.clear()
    hf_models_service._tasks.clear()
    yield
    hf_models_service._downloads.clear()
    hf_models_service._tasks.clear()


def test_group_multipart_single_file_passes_through_with_itself_as_part():
    files = [{"filename": "single.gguf", "size": 42}]

    grouped = hf_models_service.group_multipart(files)

    assert grouped == [
        {
            "filename": "single.gguf",
            "size": 42,
            "parts": ["single.gguf"],
        }
    ]


def test_group_multipart_collapses_parts_using_regex_base():
    files = [
        {"filename": "m-00002-of-00003.gguf", "size": 20},
        {"filename": "m-00003-of-00003.gguf", "size": 30},
        {"filename": "m-00001-of-00003.gguf", "size": 10},
    ]

    grouped = hf_models_service.group_multipart(files)

    assert grouped == [
        {
            "filename": "m",
            "size": 60,
            "parts": [
                "m-00001-of-00003.gguf",
                "m-00002-of-00003.gguf",
                "m-00003-of-00003.gguf",
            ],
        }
    ]


def test_group_multipart_mixed_files_are_sorted_by_filename():
    files = [
        {"filename": "z.gguf", "size": 5},
        {"filename": "m-00002-of-00002.gguf", "size": 20},
        {"filename": "a.gguf", "size": 1},
        {"filename": "m-00001-of-00002.gguf", "size": 10},
    ]

    grouped = hf_models_service.group_multipart(files)

    assert [file["filename"] for file in grouped] == ["a.gguf", "m", "z.gguf"]


def test_expand_parts_returns_parts_for_grouped_filename():
    parts = [
        "m-00001-of-00002.gguf",
        "m-00002-of-00002.gguf",
    ]
    files = [{"filename": "m", "size": 30, "parts": parts}]

    assert hf_models_service.expand_parts("owner/repo", "m", files) == parts


def test_expand_parts_returns_filename_when_group_is_not_found():
    files = [{"filename": "other.gguf", "size": 10, "parts": ["other.gguf"]}]

    assert hf_models_service.expand_parts("owner/repo", "missing.gguf", files) == [
        "missing.gguf"
    ]


def test_download_id_joins_repo_and_filename_with_double_colon():
    assert (
        hf_models_service.download_id("owner/repo", "model.gguf")
        == "owner/repo::model.gguf"
    )


def test_list_local_models_includes_single_and_first_multipart_file(
    tmp_path,
    monkeypatch,
):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "a.gguf").write_bytes(b"single")
    (model_dir / "m-00001-of-00002.gguf").write_bytes(b"first")
    (model_dir / "m-00002-of-00002.gguf").write_bytes(b"second")
    monkeypatch.setattr(
        hf_models_service,
        "get_settings",
        lambda: SimpleNamespace(litellm_config_dir=tmp_path),
    )

    models = hf_models_service.list_local_models()

    assert [model["filename"] for model in models] == [
        "a.gguf",
        "m-00001-of-00002.gguf",
    ]
    assert "m-00002-of-00002.gguf" not in {
        model["filename"] for model in models
    }


def test_cancel_download_returns_false_without_matching_task():
    assert hf_models_service.cancel_download("owner/repo::model.gguf") is False


def test_hf_headers_uses_bearer_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "secret-token")

    assert hf_models_service._hf_headers() == {
        "Authorization": "Bearer secret-token"
    }


def test_hf_headers_without_token_returns_empty_dict(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    assert hf_models_service._hf_headers() == {}


@pytest.mark.asyncio
async def test_start_download_returns_existing_active_download_without_new_task():
    did = hf_models_service.download_id("owner/repo", "model.gguf")
    existing = {
        "id": did,
        "repo_id": "owner/repo",
        "filename": "model.gguf",
        "status": "downloading",
    }
    hf_models_service._downloads[did] = existing

    with (
        patch.object(
            hf_models_service,
            "list_repo_gguf_files",
            AsyncMock(),
        ) as list_repo_files_mock,
        patch.object(hf_models_service.asyncio, "create_task") as create_task_mock,
    ):
        result = await hf_models_service.start_download(
            "owner/repo",
            "model.gguf",
        )

    assert result is existing
    list_repo_files_mock.assert_not_awaited()
    create_task_mock.assert_not_called()
    assert hf_models_service._tasks == {}
