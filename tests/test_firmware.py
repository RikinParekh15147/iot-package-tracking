"""Execute the exact C++ helpers included by the ESP32, under Arduino's word macro."""
from pathlib import Path
import os
import subprocess
import pytest

CASES = [
    "decode_signed_boundaries", "captured_mpu_frame", "arming_all_prerequisites",
    "queue_event_reservation", "timer_duration_and_single_alert",
    "timer_interruption_resets_duration", "timer_start_at_zero", "timer_rollover",
    "timer_immediate_and_rearm", "impact_threshold_and_disarmed",
    "impact_cooldown_at_zero", "impact_rollover", "tilt_known_orientations",
    "tilt_invalid_and_roundoff", "stable_motion_gating",
    "seal_evidence_combinations", "disabled_light_cannot_confirm_opening",
    "wifi_sticky_until_failure", "vibration_repetitive_signal",
    "vibration_rejects_stillness_and_single_impact", "vibration_ignores_invalid_samples",
    "console_order_and_trip_capture", "console_overflow_keeps_latest",
    "console_ack_preserves_arrivals_during_upload", "console_retry_and_partial_ack",
    "console_long_messages_are_terminated",
    "online_fast_offline_storage_interval", "server_health_clock_rollover",
]
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="session")
def native_firmware_tests(tmp_path_factory):
    import ziglang
    compiler = Path(ziglang.__file__).parent / ("zig.exe" if os.name == "nt" else "zig")
    binary = tmp_path_factory.mktemp("native-firmware") / ("monitor-tests.exe" if os.name == "nt" else "monitor-tests")
    result = subprocess.run([str(compiler), "c++", "-std=c++17", "-O0",
                             str(ROOT / "tests/firmware/test_monitor_logic.cpp"), "-o", str(binary)],
                            text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary

@pytest.mark.parametrize("case", CASES)
def test_firmware_logic(native_firmware_tests, case):
    result = subprocess.run([str(native_firmware_tests), case], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS " + case
