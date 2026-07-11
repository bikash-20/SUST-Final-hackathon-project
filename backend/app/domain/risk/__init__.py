"""Explainable, provider-isolated risk signals.

The risk domain produces behavioural advisories.  It never makes a fraud
determination and every surfaced signal requires a human review.
"""

from app.domain.risk.anomaly_detector import (
    AnomalyDetectorConfig,
    DetectionEvidence,
    DetectionResult,
    SlidingWindowAnomalyDetector,
    TransactionObservation,
)

__all__ = [
    "AnomalyDetectorConfig",
    "DetectionEvidence",
    "DetectionResult",
    "SlidingWindowAnomalyDetector",
    "TransactionObservation",
]
