"""Train the PO classifier — TF-IDF + Logistic Regression.

Standalone: depends only on the label store and the feature builder,
never on the extraction package.
"""

import json
from datetime import datetime, timezone
from typing import Any

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from classifier.dataset import NOT_PO_LABEL, PO_LABEL, load_labels
from classifier.features import build_feature_text
from classifier.loader import METADATA_FILENAME, MODEL_FILENAME
from config.settings import settings

# Minimum labels required per class before a model can be trained.
MIN_PER_CLASS = 3


class NotEnoughData(RuntimeError):
    """Raised when there are too few labels to train a usable model."""


def _build_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    max_features=20000,
                    sublinear_tf=True,
                    stop_words="english",
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )


def train_classifier() -> dict[str, Any]:
    """Train on the stored labels, save the model, and return its metadata.

    Raises :class:`NotEnoughData` when either class has fewer than
    :data:`MIN_PER_CLASS` labels.
    """
    texts: list[str] = []
    targets: list[str] = []
    for record in load_labels():
        texts.append(build_feature_text(record.get("subject", ""), record.get("body_text", "")))
        targets.append(record.get("label", ""))

    n_po = targets.count(PO_LABEL)
    n_not_po = targets.count(NOT_PO_LABEL)
    if n_po < MIN_PER_CLASS or n_not_po < MIN_PER_CLASS:
        raise NotEnoughData(
            f"Need at least {MIN_PER_CLASS} PO and {MIN_PER_CLASS} non-PO labels "
            f"to train (have {n_po} PO, {n_not_po} non-PO)."
        )

    # 80/20 stratified train/test split.
    # Force at least 2 test samples so stratification keeps both classes.
    n_test = max(2, round(0.2 * len(targets)))
    X_train, X_test, y_train, y_test = train_test_split(
        texts, targets, test_size=n_test, stratify=targets, random_state=42
    )

    pipeline = _build_pipeline()
    pipeline.fit(X_train, y_train)
    train_accuracy = float(pipeline.score(X_train, y_train))
    test_accuracy = float(pipeline.score(X_test, y_test))

    model_dir = settings.classifier_model_path
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, model_dir / MODEL_FILENAME)

    metadata = {
        "model_version": settings.classifier_model_version,
        "algorithm": "tfidf+logreg",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(targets),
        "n_po": n_po,
        "n_not_po": n_not_po,
        "split": "train_test_80_20",
        "n_train": len(X_train),
        "n_test": len(X_test),
        "train_accuracy": train_accuracy,
        "test_accuracy": test_accuracy,
    }
    (model_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
