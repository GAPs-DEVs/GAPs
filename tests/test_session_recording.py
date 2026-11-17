"""A recorded session must replay through the same tracker and explain losses."""
from tools.benchmark_tracking_recovery import scene, sequence
from tools.replay_session import replay, simplify, summarize
from tools.session_recording import SessionRecorder, grid_from_json, grid_to_json
from vision.fretboard_motion import FretboardMotionTracker


def test_grid_survives_json_round_trip():
    _, _, grid = scene()
    assert grid_from_json(grid_to_json(grid)) == grid


def test_recorded_motion_replays_tracked_and_lost_frames_report_a_cause(tmp_path):
    frame, background, grid = scene()
    # The test supplies frames far faster than a camera; keep them all queued.
    recorder = SessionRecorder(tmp_path / 'recording', queue_frames=200)
    recorder.frame(frame, 1, 10.)
    recorder.seeded(1, frame, None, grid, 9)
    number = 1
    for stage, current, _, _ in sequence(frame, background):
        if stage not in ('motion', 'lost'):
            continue
        number += 1
        recorder.frame(current, number, 10. + (number - 1) / 30)
        recorder.result(number, 'selected_lines_motion', 'live detail', 1)
    recorder.close()
    assert recorder.dropped_frames == 0

    records = replay(tmp_path / 'recording', realtime=False, hand_mask='off', background_recovery=False)

    assert len(records) == number
    assert records[0]['mode'] == 'calibration_required'
    motion, lost = records[1:25], records[25:]
    assert all(record['mode'] == 'selected_lines_motion' for record in motion)
    assert all(record['detail'].endswith('pose=ok') for record in motion)
    assert all(record['mode'] in {'selected_lines_hold', 'selected_lines_lost'} for record in lost)
    assert all('pose=none' in record['detail'] and 'inc=' in record['detail'] for record in lost)
    assert records[1]['live_detail'] == 'live detail'

    report = summarize(records)
    assert report['dropouts'] == 1 and report['hidden_frames'] == len(lost)
    assert report['hidden_causes']


def test_motion_tracker_names_the_gate_that_rejected_a_frame():
    frame, _, grid = scene()
    tracker = FretboardMotionTracker()
    assert tracker.estimate(frame, grid) is None
    assert tracker.last_rejection == 'no_points'
    tracker.commit(frame, grid, refresh=True)
    assert tracker.estimate(frame, grid) is not None
    assert tracker.last_rejection == ''


def test_cause_grouping_ignores_per_frame_numbers():
    assert simplify('inc=none(few_tracked:5) disagree=3.2/2.0') == simplify('inc=none(few_tracked:9) disagree=4.0/2.0')
