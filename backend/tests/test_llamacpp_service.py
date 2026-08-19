from unittest.mock import AsyncMock, patch

import pytest

from app.models.provider import Provider
from app.services import llamacpp_service


DEVICE_OUTPUT = (
    "Available devices:\n"
    "  Vulkan0: AMD Radeon AI PRO R9700 (32768 MiB, 31900 MiB free)\n"
    "  Vulkan1: AMD Radeon RX 7800 XT (16384 MiB, 16100 MiB free)\n"
)


@pytest.fixture
def provider_factory():
    def create(local_launch=None, api_base="http://127.0.0.1:4002"):
        return Provider(
            id="llamacpp",
            name="Local llama.cpp",
            api_base=api_base,
            local_launch=local_launch or {},
        )

    return create


def test_parse_devices_returns_expected_device_fields():
    devices = llamacpp_service.parse_devices(DEVICE_OUTPUT)

    assert devices == [
        {
            "index": 0,
            "backend": "Vulkan",
            "name": "AMD Radeon AI PRO R9700",
            "vram_total_mib": 32768,
            "vram_free_mib": 31900,
        },
        {
            "index": 1,
            "backend": "Vulkan",
            "name": "AMD Radeon RX 7800 XT",
            "vram_total_mib": 16384,
            "vram_free_mib": 16100,
        },
    ]


def test_parse_devices_ignores_garbage_lines():
    output = "garbage\n" + DEVICE_OUTPUT + "VulkanX: malformed device\n"

    devices = llamacpp_service.parse_devices(output)

    assert [device["index"] for device in devices] == [0, 1]


def test_parse_devices_empty_output_returns_empty_list():
    assert llamacpp_service.parse_devices("") == []


def test_compute_tensor_split_is_proportional_and_normalized():
    devices = [
        {"vram_free_mib": 31900},
        {"vram_free_mib": 16100},
    ]

    split = llamacpp_service.compute_tensor_split(devices)

    assert split == pytest.approx([0.665, 0.335], abs=0.001)
    assert sum(split) == pytest.approx(1.0)


def test_compute_tensor_split_empty_devices_returns_empty_list():
    assert llamacpp_service.compute_tensor_split([]) == []


def test_compute_tensor_split_all_zero_free_vram_returns_empty_list():
    devices = [
        {"vram_free_mib": 0},
        {"vram_free_mib": 0},
    ]

    assert llamacpp_service.compute_tensor_split(devices) == []


def test_port_from_api_base_uses_explicit_port():
    assert llamacpp_service.port_from_api_base("http://127.0.0.1:4002") == 4002


def test_port_from_api_base_without_port_uses_default():
    assert (
        llamacpp_service.port_from_api_base("http://x/v1")
        == llamacpp_service.DEFAULT_PORT
    )


def test_estimate_fit_accounts_for_model_and_context_size(tmp_path):
    model_path = tmp_path / "model.gguf"
    with model_path.open("wb") as model_file:
        model_file.truncate(20 * 1024 * 1024)
    devices = [{"vram_free_mib": 70}]

    without_context = llamacpp_service.estimate_fit(str(model_path), 0, devices)
    with_context = llamacpp_service.estimate_fit(str(model_path), 1000, devices)

    assert without_context["needed_mib"] == 23
    assert with_context == {
        "fits": True,
        "needed_mib": 63,
        "available_mib": 70,
    }
    assert with_context["needed_mib"] - without_context["needed_mib"] == 40


def test_estimate_fit_missing_model_never_fits(tmp_path):
    missing_model = tmp_path / "missing.gguf"

    estimate = llamacpp_service.estimate_fit(
        str(missing_model),
        100,
        [{"vram_free_mib": 999}],
    )

    assert estimate == {
        "fits": False,
        "needed_mib": 4,
        "available_mib": 999,
    }


def test_build_cmdline_single_device_uses_defaults_without_tensor_split(
    provider_factory,
):
    provider = provider_factory(
        local_launch={
            "exe_path": "llama-server",
            "model_path": "model.gguf",
        }
    )

    cmdline = llamacpp_service.build_cmdline(
        provider,
        [{"vram_free_mib": 31900}],
    )

    assert "--tensor-split" not in cmdline
    assert "--split-mode" not in cmdline
    assert cmdline[cmdline.index("--ctx-size") + 1] == "32768"
    assert cmdline[cmdline.index("--n-gpu-layers") + 1] == "999"
    assert "--jinja" in cmdline
    assert "--slots" in cmdline


def test_build_cmdline_two_devices_uses_auto_tensor_split(provider_factory):
    provider = provider_factory(
        local_launch={
            "exe_path": "llama-server",
            "model_path": "model.gguf",
            "tensor_split": "auto",
        }
    )
    devices = [
        {"vram_free_mib": 31900},
        {"vram_free_mib": 16100},
    ]

    cmdline = llamacpp_service.build_cmdline(provider, devices)

    assert cmdline[cmdline.index("--split-mode") + 1] == "layer"
    assert cmdline[cmdline.index("--tensor-split") + 1] == "0.665,0.335"
    assert "--jinja" in cmdline
    assert "--slots" in cmdline


def test_build_cmdline_respects_manual_tensor_split(provider_factory):
    provider = provider_factory(
        local_launch={
            "exe_path": "llama-server",
            "model_path": "model.gguf",
            "tensor_split": [2, 1],
        }
    )
    devices = [
        {"vram_free_mib": 1},
        {"vram_free_mib": 1},
    ]

    cmdline = llamacpp_service.build_cmdline(provider, devices)

    assert cmdline[cmdline.index("--tensor-split") + 1] == "2.0,1.0"
    assert "--jinja" in cmdline
    assert "--slots" in cmdline


@pytest.mark.asyncio
async def test_stop_llamacpp_refuses_busy_server_without_force(provider_factory):
    provider = provider_factory()
    status = {"running": True, "busy_slots": 2}

    with (
        patch.object(
            llamacpp_service,
            "get_status",
            AsyncMock(return_value=status),
        ),
        patch.object(llamacpp_service.psutil, "Process") as process_mock,
        patch.object(llamacpp_service.psutil, "process_iter") as process_iter_mock,
    ):
        with pytest.raises(RuntimeError, match=r"2 slot\(s\) procesando"):
            await llamacpp_service.stop_llamacpp(provider)

    process_mock.assert_not_called()
    process_iter_mock.assert_not_called()


@pytest.mark.asyncio
async def test_stop_llamacpp_force_kills_busy_server(
    tmp_path,
    provider_factory,
):
    provider = provider_factory()
    status = {"running": True, "busy_slots": 2}
    pid_file = tmp_path / "llamacpp.pid"
    pid_file.write_text("4321", encoding="utf-8")

    with (
        patch.object(
            llamacpp_service,
            "get_status",
            AsyncMock(return_value=status),
        ),
        patch.object(llamacpp_service, "_read_pid", return_value=4321),
        patch.object(llamacpp_service, "_pid_file", return_value=pid_file),
        patch.object(llamacpp_service.psutil, "Process") as process_mock,
        patch.object(
            llamacpp_service.psutil,
            "process_iter",
            return_value=[],
        ) as process_iter_mock,
    ):
        result = await llamacpp_service.stop_llamacpp(provider, force=True)

    assert result == {"stopped": True, "killed": 1}
    process_mock.assert_called_once_with(4321)
    process_mock.return_value.kill.assert_called_once_with()
    process_iter_mock.assert_called_once_with(["pid", "name", "cmdline"])
    assert not pid_file.exists()


def test_build_cmdline_router_mode_serves_models_dir(provider_factory, tmp_path, monkeypatch):
    from app.services import hf_models_service

    monkeypatch.setattr(hf_models_service, "models_dir", lambda: tmp_path)
    provider = provider_factory(local_launch={"router_mode": True})

    cmd = llamacpp_service.build_cmdline(provider, [])

    assert "--model" not in cmd
    assert "--models-dir" in cmd
    assert cmd[cmd.index("--models-dir") + 1] == str(tmp_path)
