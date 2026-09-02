import time

from backend.state import AlertStateManager


def test_backoff_exponential_and_success_reset(tmp_path):
    state = AlertStateManager(state_path=str(tmp_path / "runtime_state.json"))

    assert state.get_task_backoff("task-1") == (0, 0)

    state.mark_task_failure("task-1")
    fail_count, next_ok_ts = state.get_task_backoff("task-1")
    assert fail_count == 1
    assert next_ok_ts > time.time()  # 5 分钟后可重试

    state.mark_task_failure("task-1")
    fail_count, next_ok_ts = state.get_task_backoff("task-1")
    assert fail_count == 2
    assert next_ok_ts > time.time() + 9 * 60  # 10 分钟档

    state.mark_task_success("task-1")
    assert state.get_task_backoff("task-1") == (0, 0)


def test_backoff_caps_at_one_hour():
    delay = AlertStateManager._backoff_delay_seconds(6)  # 5*2^5=160min -> 封顶 60
    assert delay == 3600
