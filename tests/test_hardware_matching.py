"""Hardware rules and input errors without a database."""

from dataclasses import replace
from itertools import product
from unittest.mock import MagicMock

import pytest

from app.matching import GPU, InvalidJobID, JobNotFound, JobRequirement, MissingJobRequirement, parse_job_id
from app.repositories.hardware import HardwareRepository
from app.services.hardware import check_hardware, evaluate_hardware


def requirement(count=2, same=True):
    return JobRequirement(1, 1, "NVIDIA", "H100-SXM-80GB", count, same)


def gpu(number, server=1, model=1, hardware="HEALTHY", operational="ONLINE"):
    return GPU(number, f"TEST-{number}", model, hardware, server, f"server-{server}", operational)


@pytest.mark.parametrize("count,same,status", [
    (2, True, "HARDWARE_FEASIBLE"),
    (4, False, "HARDWARE_FEASIBLE"),
    (4, True, "HARDWARE_INSUFFICIENT"),
    (1, False, "HARDWARE_FEASIBLE"),
    (6, False, "HARDWARE_INSUFFICIENT"),
])
def test_placement_rules(count, same, status):
    inventory = (gpu(1), gpu(2), gpu(3), gpu(4, server=2), gpu(5, server=2))
    result = evaluate_hardware(requirement(count, same), inventory)
    assert result.status == status
    assert result.eligible_gpu_count == 5
    assert result.max_gpus_per_server == 3
    assert [capacity.healthy_gpu_count for capacity in result.server_capacities] == [3, 2]
    if same and count == 4:
        assert result.reason_codes == ("SAME_SERVER_SHORTAGE",)
        assert result.matching_server_ids == ()
        assert "동일 서버" in result.explanation


@pytest.mark.parametrize("hardware,operational", list(product(
    ["HEALTHY", "FAULTY", "MAINTENANCE"], ["ONLINE", "MAINTENANCE", "OFFLINE"])))
def test_only_online_healthy_gpus_qualify(hardware, operational):
    result = evaluate_hardware(requirement(1), [gpu(1, hardware=hardware, operational=operational)])
    eligible = hardware == "HEALTHY" and operational == "ONLINE"
    assert result.eligible_gpu_count == int(eligible)
    assert result.status == ("HARDWARE_FEASIBLE" if eligible else "HARDWARE_INSUFFICIENT")
    assert len(result.model_gpus) == 1  # Excluded equipment remains inspectable.
    assert len(result.candidate_gpus) == int(eligible)


def test_different_model_never_substitutes_for_original():
    original = requirement(1, False)
    result = evaluate_hardware(original, [gpu(1, model=2), gpu(2, model=2)])
    assert result.status == "HARDWARE_INSUFFICIENT"
    assert result.reason_codes == ("MODEL_UNAVAILABLE", "QUANTITY_SHORTAGE")
    assert "H100-SXM-80GB" in result.explanation
    assert result.model_gpus == result.candidate_gpus == ()
    assert original == requirement(1, False)


def test_empty_inventory_explains_model_quantity_and_placement():
    result = evaluate_hardware(requirement(), [])
    assert result.reason_codes == ("MODEL_UNAVAILABLE", "QUANTITY_SHORTAGE", "SAME_SERVER_SHORTAGE")
    assert result.eligible_gpu_count == result.max_gpus_per_server == 0
    assert "2개 부족" in result.explanation


def test_distributed_request_can_fit_in_one_server_and_is_deterministic():
    inventory = [gpu(2), gpu(1)]
    result = evaluate_hardware(requirement(2, False), inventory)
    assert result.status == "HARDWARE_FEASIBLE"
    assert result.matching_server_ids == (1,)
    assert [item.gpu_id for item in result.candidate_gpus] == [1, 2]
    assert inventory == [gpu(2), gpu(1)]
    assert "할당·예약 정보는 검사하지 않으며" in result.scope


@pytest.mark.parametrize("value,expected", [(1, 1), ("001", 1), ("18446744073709551615", 18446744073709551615)])
def test_valid_job_ids(value, expected):
    assert parse_job_id(value) == expected


@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, None, "", " ", "1.0", "-1", "+1",
                                      "１", "1 OR 1=1", "1; DROP TABLE gpus", "9" * 5000,
                                      18446744073709551616, "18446744073709551616"])
def test_invalid_ids_rejected_before_repository_access(value):
    repo = MagicMock()
    with pytest.raises(InvalidJobID):
        check_hardware(repo, value)
    repo.get_requirement.assert_not_called()
    repo.list_model_gpus.assert_not_called()


def test_service_uses_original_model_id():
    repo = MagicMock()
    repo.get_requirement.return_value = replace(requirement(1), gpu_model_id=42)
    repo.list_model_gpus.return_value = [gpu(1, model=42)]
    assert check_hardware(repo, "001").status == "HARDWARE_FEASIBLE"
    repo.get_requirement.assert_called_once_with(1)
    repo.list_model_gpus.assert_called_once_with(42)


@pytest.mark.parametrize("row,error", [(None, JobNotFound), ((1, None, None, None, None, None), MissingJobRequirement)])
def test_repository_distinguishes_missing_job_and_requirement(row, error):
    connection = MagicMock()
    connection.cursor.return_value.fetchone.return_value = row
    with pytest.raises(error):
        HardwareRepository(connection).get_requirement(1)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_repository_parameters_are_bound_and_inventory_keeps_states():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = (1, 2, "NVIDIA", "A100-SXM-80GB", 3, 0)
    cursor.fetchall.return_value = [(7, "A-7", 2, "HEALTHY", 4, "s4", "MAINTENANCE")]
    repo = HardwareRepository(connection)
    request = repo.get_requirement("001")
    assert request.gpu_model_id == 2 and request.same_server_required is False
    assert repo.list_model_gpus(2)[0].operational_status == "MAINTENANCE"
    calls = cursor.execute.call_args_list
    assert [call.args[1] for call in calls] == [(1,), (2,)]
    assert all(call.args[0].startswith("SELECT ") and "%s" in call.args[0] for call in calls)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
