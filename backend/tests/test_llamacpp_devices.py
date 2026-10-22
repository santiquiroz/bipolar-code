"""Selección de dispositivos llama.cpp: parseo, select_devices y --device."""

from app.models.provider import Provider
from app.services.llamacpp_service import (
    build_cmdline,
    parse_devices,
    select_devices,
)

SAMPLE_OUTPUT = (
    "Available devices:\n"
    "  Vulkan0: AMD Radeon(TM) Graphics (16384 MiB, 16000 MiB free)\n"
    "  Vulkan1: AMD Radeon RX 7800 XT (16368 MiB, 15000 MiB free)\n"
)


def _provider(**launch):
    return Provider(
        id="llamacpp",
        name="Local llama.cpp",
        api_base="http://127.0.0.1:4002",
        local_launch={
            "exe_path": "llama-server",
            "model_path": "model.gguf",
            **launch,
        },
    )


def test_parse_devices_adds_id_and_integrated_flags():
    devices = parse_devices(SAMPLE_OUTPUT)

    assert [d["id"] for d in devices] == ["Vulkan0", "Vulkan1"]
    assert [d["integrated"] for d in devices] == [True, False]


def test_select_devices_auto_excludes_integrated():
    devices = parse_devices(SAMPLE_OUTPUT)

    assert [d["id"] for d in select_devices(devices, "auto")] == ["Vulkan1"]
    assert [d["id"] for d in select_devices(devices)] == ["Vulkan1"]


def test_select_devices_respects_list_order():
    devices = parse_devices(SAMPLE_OUTPUT)

    selected = select_devices(devices, ["Vulkan1", "Vulkan0"])

    assert [d["id"] for d in selected] == ["Vulkan1", "Vulkan0"]


def test_select_devices_ignores_unknown_ids():
    devices = parse_devices(SAMPLE_OUTPUT)

    selected = select_devices(devices, ["Vulkan9", "Vulkan1"])

    assert [d["id"] for d in selected] == ["Vulkan1"]


def test_select_devices_without_matches_falls_back_to_auto():
    devices = parse_devices(SAMPLE_OUTPUT)

    assert [d["id"] for d in select_devices(devices, ["Vulkan9"])] == ["Vulkan1"]


def test_select_devices_all_integrated_returns_all():
    devices = [
        {"id": "Vulkan0", "integrated": True},
        {"id": "Vulkan1", "integrated": True},
    ]

    assert select_devices(devices, "auto") == devices


def test_build_cmdline_auto_selects_discrete_without_tensor_split():
    cmdline = build_cmdline(_provider(), parse_devices(SAMPLE_OUTPUT))

    assert cmdline[cmdline.index("--device") + 1] == "Vulkan1"
    assert "--tensor-split" not in cmdline
    assert "--split-mode" not in cmdline


def test_build_cmdline_explicit_order_sets_device_and_ordered_split():
    cmdline = build_cmdline(
        _provider(devices=["Vulkan1", "Vulkan0"]),
        parse_devices(SAMPLE_OUTPUT),
    )

    assert cmdline[cmdline.index("--device") + 1] == "Vulkan1,Vulkan0"
    assert cmdline[cmdline.index("--tensor-split") + 1] == "0.484,0.516"
