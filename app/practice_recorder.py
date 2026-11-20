from datetime import datetime, timezone
import uuid


class PracticeResultCollector:

  def __init__(self, score_version_id: str, speed_multiplier: float = 1.0):
    self.client_record_id = str(uuid.uuid4())
    self.score_version_id = score_version_id
    self.speed_multiplier = round(speed_multiplier, 2)
    self.started_at = datetime.now(timezone.utc).isoformat()
    self.started_step_no = 1
    self.results = []
    self.attempt_counters = {}  # step_id별 attempt_no 관리

  def record_attempt(
      self,
      score_step_id: str,
      outcome: str,
      elapsed_ms: int = 0,
      error_type: str = None,
  ):
    """판정 발생 시 호출: outcome = 'correct' | 'incorrect' | 'skipped' | 'timeout'"""
    # attempt_no 1부터 자동 증가
    current_attempt = self.attempt_counters.get(score_step_id, 0) + 1
    self.attempt_counters[score_step_id] = current_attempt

    item = {
        "score_step_id": score_step_id,
        "attempt_no": current_attempt,
        "outcome": outcome,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
    }
    if error_type:
      item["error_type"] = error_type

    self.results.append(item)

  def build_payload(
      self,
      status: str,
      active_duration_ms: int,
      target_tempo_bpm: float = 90.0,
  ):
    """status: 'completed' | 'abandoned'"""
    ended_at = datetime.now(timezone.utc).isoformat()
    return {
        "client_record_id": self.client_record_id,
        "score_version_id": self.score_version_id,
        "mode": "interactive",
        "status": status,
        "speed_multiplier": self.speed_multiplier,
        "started_step_no": self.started_step_no,
        "started_at": self.started_at,
        "ended_at": ended_at,
        "active_duration_ms": max(0, active_duration_ms),
        "target_tempo_bpm": round(target_tempo_bpm, 2),
        "client_version": "1.0.0",
        "results": self.results,
    }