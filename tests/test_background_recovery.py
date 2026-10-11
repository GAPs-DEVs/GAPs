"""Slow descriptor recovery cannot block video or publish obsolete coordinates."""
from threading import Event, Thread
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from test_fretboard_motion import scene, translation, warp
from vision.fretboard_mapping import SelectedLineTracker
from vision.fretboard_motion import HybridSelectedLineTracker, grid_quad
from vision.fretboard_recovery import BackgroundFretboardRecovery, RecoveryRequest, RecoveryResult
from vision.fretboard_reference import FretboardVisualReference


def wait_idle(recovery):
    with recovery._condition:
        assert recovery._condition.wait_for(
            lambda: not recovery._active and recovery._pending is None, timeout=3.,
        ), 'Background recovery failed to finish'


@pytest.fixture
def live_tracker():
    frame, grid = scene()
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    recovery = BackgroundFretboardRecovery()
    tracker = HybridSelectedLineTracker(selected, recovery=recovery)
    tracker.seed(frame)
    try:
        yield frame, grid, tracker
    finally:
        tracker.close()


def result_for(tracker, frame, pose, *, timestamp=10., frame_index=1):
    request = RecoveryRequest(1, frame_index, timestamp, frame.copy(), None,
                              tracker.reference.recovery_snapshot(), None, 1280)
    return RecoveryResult(request, pose, 60.)


def test_reference_snapshot_survives_recalibration_without_sharing_mutable_cache(live_tracker):
    frame, grid, tracker = live_tracker
    snapshot = tracker.reference.recovery_snapshot()
    assert snapshot._sift is not tracker.reference._sift
    assert snapshot._frame_values is not tracker.reference._frame_values
    with tracker.reference.frame_scope(frame):
        with snapshot.frame_scope(frame):
            assert snapshot.validate(frame, np.eye(3))
            assert tracker.reference.validate(frame, np.eye(3))
    tracker.reference.clear()
    assert snapshot.validate(frame, np.eye(3))
    assert not snapshot.template.flags.writeable


def test_blocked_search_keeps_video_updates_and_brief_blur_markers_live(live_tracker, monkeypatch):
    frame, grid, tracker = live_tracker
    started, release, updated = Event(), Event(), Event()
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    tracker.last_verified_at = now[0]

    def blocked(request):
        started.set()
        assert release.wait(3.)
        return RecoveryResult(request, None, 60.)

    tracker.recovery._search = blocked
    tracker.reference.estimate = Mock(side_effect=AssertionError('SIFT ran on video thread'))
    results = []

    def update_frames():
        for index in range(1, 5):
            now[0] += 1 / 30
            current = cv2.GaussianBlur(warp(frame, translation(index * 1.5, index * .4)), (11, 11), 0)
            results.append(tracker.update(current, lambda roi: (), timestamp=now[0]))
        updated.set()

    thread = Thread(target=update_frames, daemon=True)
    thread.start()
    try:
        assert started.wait(3.)
        assert updated.wait(3.), 'Video waited for background SIFT'
        assert not release.is_set()
        assert len(results) == 4
        for index, result in enumerate(results, 1):
            assert result.result.tracking_mode == 'selected_lines_continuity'
            np.testing.assert_allclose(grid_quad(result.result.stable_grid),
                                       grid_quad(grid) + (index * 1.5, index * .4), atol=.8)
    finally:
        release.set()
        thread.join(timeout=3.)


def test_pending_mailbox_replaces_old_images_and_copies_backend_pixels(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    started, release = Event(), Event()
    requests = []

    def blocked(request):
        requests.append(request)
        if len(requests) == 1:
            started.set()
            assert release.wait(3.)
        return RecoveryResult(request, None, 1.)

    recovery._search = blocked
    shared = frame.copy()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    try:
        assert recovery.submit(shared, mask, np.eye(3), timestamp=10., frame_index=1)
        assert started.wait(3.)
        shared[:] = 42
        assert recovery.submit(shared, mask, np.eye(3), timestamp=10.3, frame_index=2)
        shared[:] = 99
        assert recovery.submit(shared, mask, np.eye(3), timestamp=10.6, frame_index=3)
        shared[:] = 0
        mask[:] = 255
    finally:
        release.set()
    wait_idle(recovery)
    assert [request.frame_index for request in requests] == [1, 3]
    np.testing.assert_array_equal(requests[0].frame, frame)
    assert np.all(requests[1].frame == 99)
    assert not requests[1].exclusion_mask.any()


def test_recalibration_discards_inflight_and_queued_results(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    started, release = Event(), Event()
    requests = []

    def blocked(request):
        requests.append(request)
        started.set()
        assert release.wait(3.)
        return RecoveryResult(request, np.eye(3), 1.)

    recovery._search = blocked
    try:
        recovery.submit(frame, None, np.eye(3), timestamp=10., frame_index=1)
        assert started.wait(3.)
        recovery.submit(frame, None, np.eye(3), timestamp=10.3, frame_index=2)
        tracker.seed(frame)
    finally:
        release.set()
    wait_idle(recovery)
    assert len(requests) == 1
    assert recovery.poll() is None
    assert tracker.pending_recovery is None


def test_search_cadence_is_time_based_and_escalates_after_local_failure(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    requests = []

    def failed(request):
        requests.append(request)
        return RecoveryResult(request, None, 1.)

    recovery._search = failed
    for index in range(121):
        recovery.submit(frame, None, np.eye(3), timestamp=10 + index / 120, frame_index=index)
        wait_idle(recovery)
        recovery.poll()
    global_requests = [request for request in requests if request.roi is None]
    assert requests[0].roi is not None
    assert len(global_requests) == 2
    assert global_requests[1].timestamp - global_requests[0].timestamp >= .5
    assert [request.max_side for request in global_requests] == [640, 960]
    assert len(requests) <= 8


def test_changed_scene_gets_prompt_search_without_removing_global_rate_limit(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    requests = []

    def failed(request):
        requests.append(request)
        return RecoveryResult(request, None, 1.)

    recovery._search = failed
    for index, (timestamp, image) in enumerate((
        (10., frame), (10.01, frame),
        (10.17, np.full_like(frame, 100)), (10.18, np.full_like(frame, 200)),
    )):
        recovery.submit(image, None, np.eye(3), timestamp=timestamp, frame_index=index)
        wait_idle(recovery)
        recovery.poll()
    global_requests = [request for request in requests if request.roi is None]
    assert [request.timestamp for request in global_requests] == [10.01, 10.17]


@pytest.mark.parametrize('mask_all', [False, True])
def test_background_wait_preserves_short_blur_but_never_coasts_after_hard_loss(live_tracker, monkeypatch, mask_all):
    frame, _, tracker = live_tracker
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    tracker.last_verified_at = now[0]
    tracker.recovery.submit = Mock(return_value=True)
    tracker.recovery.poll = Mock(return_value=None)
    blurred = cv2.GaussianBlur(frame, (11, 11), 0)
    now[0] += .1
    assert tracker.update(blurred, lambda roi: ()).result.tracking_mode == 'selected_lines_continuity'
    mask = np.full(frame.shape[:2], 255, dtype=np.uint8) if mask_all else None
    lost = tracker.update(frame if mask_all else np.zeros_like(frame), lambda roi: (), mask)
    assert lost.homography is None
    assert tracker.update(blurred, lambda roi: ()).homography is None
    assert tracker.update(frame, lambda roi: ()).result.tracking_mode == 'selected_lines_reacquired'


def test_background_continuity_cannot_extend_its_deadline(live_tracker, monkeypatch):
    frame, _, tracker = live_tracker
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    tracker.last_verified_at = now[0]
    tracker.recovery.submit = Mock(return_value=True)
    tracker.recovery.poll = Mock(return_value=None)
    blurred = cv2.GaussianBlur(frame, (11, 11), 0)
    now[0] += .1
    assert tracker.update(blurred, lambda roi: ()).homography is not None
    now[0] = 10.251
    assert tracker.update(blurred, lambda roi: ()).homography is None
    assert tracker.last_verified_at == 10.


def test_delayed_result_is_transported_to_current_moving_guitar(live_tracker):
    frame, grid, tracker = live_tracker
    tracker.frame_index = 5
    source = warp(frame, translation(110, 0))
    result = result_for(tracker, source, translation(110, 0))
    current = warp(frame, translation(122, 3))
    pose = tracker._transport_recovery(result, current, 10.1, None)
    assert pose is not None
    actual = cv2.perspectiveTransform(grid_quad(grid)[:, None], pose).reshape(-1, 2)
    np.testing.assert_allclose(actual, grid_quad(grid) + (122, 3), atol=1.)


@pytest.mark.parametrize('elapsed,blank,covered', [(.6, False, False), (-.1, False, False),
                                                  (.1, True, False), (.1, False, True)])
def test_delayed_results_cannot_restore_stale_or_missing_board(live_tracker, elapsed, blank, covered):
    frame, _, tracker = live_tracker
    tracker.frame_index = 5
    source = warp(frame, translation(110, 0))
    result = result_for(tracker, source, translation(110, 0))
    current = np.zeros_like(source) if blank else source
    mask = np.full(source.shape[:2], 255, dtype=np.uint8) if covered else None
    assert tracker._transport_recovery(result, current, 10. + elapsed, mask) is None


def test_real_background_search_recovers_large_motion_without_recalibration(live_tracker):
    frame, grid, tracker = live_tracker
    tracker.reference.estimate = Mock(side_effect=AssertionError('SIFT ran on video thread'))
    visible = []
    for index in range(24):
        x = 110 + index * 1.5
        current = warp(frame, translation(x, 0))
        result = tracker.update(current, lambda roi: (), timestamp=10 + index / 30)
        wait_idle(tracker.recovery)
        visible.append(result.homography is not None)
        if result.homography is not None:
            np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid) + (x, 0), atol=1.)
    assert any(visible[:12]), 'Recovery took more than 400 ms of camera time'
    assert all(visible[-8:]), 'Recovered AR guides kept blinking'
    assert tracker.anchor_grid == grid


def test_current_verified_flow_wins_over_late_wrong_recovery(live_tracker):
    frame, grid, tracker = live_tracker
    tracker.recovery.poll = Mock(return_value=result_for(tracker, frame, translation(40, 0)))
    result = tracker.update(frame, lambda roi: (), timestamp=10.1)
    np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid), atol=.1)


def test_worker_search_failure_is_reported_and_next_search_can_run(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    recovery._reference.estimate = Mock(side_effect=RuntimeError('test search failure'))
    recovery.submit(frame, None, np.eye(3), timestamp=10., frame_index=1)
    wait_idle(recovery)
    result = recovery.poll()
    assert result.pose is None and result.error == 'test search failure'
    recovery._reference.estimate = Mock(return_value=np.eye(3))
    recovery.submit(frame, None, np.eye(3), timestamp=10.3, frame_index=2)
    wait_idle(recovery)
    assert recovery.poll().pose is not None


def test_changed_scene_retries_native_descriptors_inside_background_job(live_tracker):
    frame, _, tracker = live_tracker
    reference = tracker.reference.recovery_snapshot()
    reference.estimate = Mock(side_effect=[None, np.eye(3)])
    request = RecoveryRequest(1, 1, 10., frame, None, reference, None, 640, retry_native=True)
    result = tracker.recovery._search(request)
    assert result.pose is not None and result.error is None
    assert [call.kwargs['max_side'] for call in reference.estimate.call_args_list] == [640, 1280]


def test_camera_failure_closes_recovery_worker(monkeypatch):
    from app.features.video import PracticeVideoWorker
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, None, False, False, False)
    capture = Mock()
    capture.read.side_effect = RuntimeError('camera disconnected')
    monkeypatch.setattr('app.features.video.LatestFrameCapture', lambda _: capture)
    monkeypatch.setattr('app.features.video.load_yolo_model', lambda _: object())
    errors = []
    worker.error_message.connect(errors.append)
    worker.run()
    assert errors == ['camera disconnected']
    capture.close.assert_called_once()
    assert worker.hybrid_tracker.recovery._closed
    assert worker._capture is None


def test_clear_request_invalidates_reference_on_video_thread_before_next_frame():
    from app.features.video import PracticeVideoWorker
    frame, grid = scene()
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, None, False, False, False)
    worker.selected_line_tracker.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    worker.hybrid_tracker.seed(frame)
    generation = worker.hybrid_tracker.recovery._generation
    try:
        worker.clear_manual_tracking()
        # UI request must not mutate an in-use frame_scope or reference cache.
        assert worker.hybrid_tracker.reference.is_ready
        worker._run_frames(object(), Mock(read=Mock(return_value=None)))
        assert not worker.hybrid_tracker.reference.is_ready
        assert worker.hybrid_tracker.recovery._generation > generation
        assert worker.pending_manual_selection is None
    finally:
        worker.hybrid_tracker.close()


def test_close_waits_for_active_search_drops_pending_and_refuses_new_work(live_tracker):
    frame, _, tracker = live_tracker
    recovery = tracker.recovery
    started, release, closed = Event(), Event(), Event()

    def blocked(request):
        started.set()
        assert release.wait(3.)
        return RecoveryResult(request, np.eye(3), 1.)

    recovery._search = blocked
    recovery.submit(frame, None, np.eye(3), timestamp=10., frame_index=1)
    assert started.wait(3.)
    closer = Thread(target=lambda: (recovery.close(), closed.set()), daemon=True)
    closer.start()
    try:
        assert not closed.is_set()
    finally:
        release.set()
        assert closed.wait(3.)
        closer.join(timeout=3.)
    assert not recovery.busy
    assert recovery.poll() is None
    assert not recovery.submit(frame, None, np.eye(3), timestamp=11., frame_index=2)


def test_cropped_scaled_descriptor_coordinates_return_in_camera_space():
    from test_fretboard_reference import scene as textured_scene
    frame, grid = textured_scene()
    frame = cv2.copyMakeBorder(frame, 0, 120, 0, 660, cv2.BORDER_CONSTANT)
    reference = FretboardVisualReference()
    reference.seed(frame, grid)
    matrix = translation(160, 40)
    current = warp(frame, matrix)
    pose = reference.estimate(current, roi=(200, 20, 650, 700), max_side=512)
    assert pose is not None
    actual = cv2.perspectiveTransform(grid_quad(grid)[:, None], pose).reshape(-1, 2)
    np.testing.assert_allclose(actual, grid_quad(grid) + (160, 40), atol=1.5)
    assert reference.estimate(current, roi=(0, 0, 5, 5)) is None
