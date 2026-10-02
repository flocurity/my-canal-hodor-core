from unittest.mock import Mock

import pytest

from mycanal_hodor_core.timing import timeit


def test_timing_preserves_metadata_arguments_and_return(monkeypatch):
    logger = Mock()
    monkeypatch.setattr('mycanal_hodor_core.timing.log', logger)
    monkeypatch.setattr('mycanal_hodor_core.timing.time.perf_counter', Mock(side_effect=[10.0, 10.018]))
    result = object()

    def example(value: object, *, enabled: bool = True) -> object:
        """Example documentation."""
        assert enabled is False
        return value

    decorated = timeit()(example)
    assert decorated(result, enabled=False) is result
    assert decorated.__name__ == example.__name__
    assert decorated.__doc__ == example.__doc__
    assert decorated.__annotations__ == example.__annotations__
    assert decorated.__wrapped__ is example
    logger.debug.assert_called_once_with(
        'function_timing', function=example.__qualname__, duration_s=0.018,
    )
    assert isinstance(logger.debug.call_args.kwargs['duration_s'], float)


def test_failed_call_is_timed_and_original_exception_propagates(monkeypatch):
    logger = Mock()
    monkeypatch.setattr('mycanal_hodor_core.timing.log', logger)
    monkeypatch.setattr('mycanal_hodor_core.timing.time.perf_counter', Mock(side_effect=[5.0, 5.25]))
    error = ValueError('original failure')

    @timeit()
    def fail():
        raise error

    with pytest.raises(ValueError) as caught:
        fail()
    assert caught.value is error
    logger.debug.assert_called_once_with(
        'function_timing', function=fail.__qualname__, duration_s=0.25,
    )


def test_logging_failure_does_not_mask_function_exception(monkeypatch):
    logger = Mock()
    logger.debug.side_effect = OSError('closed output')
    monkeypatch.setattr('mycanal_hodor_core.timing.log', logger)
    monkeypatch.setattr('mycanal_hodor_core.timing.time.perf_counter', Mock(side_effect=[0.0, 1.0]))
    error = RuntimeError('original failure')

    @timeit()
    def fail():
        raise error

    with pytest.raises(RuntimeError) as caught:
        fail()
    assert caught.value is error
    logger.debug.assert_called_once()
